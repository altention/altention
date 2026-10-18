"""ops/doctor.py's whole main flow, with a made-up machine list and the real probe run in throwaway homes.

Every later step is checked with doctor, and once it died on a KeyError nobody had run (635dc09): this runs it."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from shlex import quote as shlex_quote
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import doctor  # noqa: E402


class MainFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.homes = {}
        for name in ("box-a", "空白 box b"):  # a home path with Chinese and a space
            home = os.path.join(self.tmp, name)
            os.makedirs(os.path.join(home, "altention", "engine"))
            subprocess.run(["git", "init", "-q", os.path.join(home, "altention", "engine")], check=True)
            engine = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            os.symlink(os.path.join(engine, "altention"), os.path.join(home, "altention", "engine", "altention"))
            self.homes[name] = home
        # box-a's sync has run once: the checks of the links it makes run too
        os.makedirs(os.path.join(self.homes["box-a"], ".local", "state", "altention"))
        with open(os.path.join(self.homes["box-a"], ".local", "state", "altention", "sync.json"), "w") as f:
            json.dump({"links": {}}, f)
        self.instance = os.path.join(self.tmp, "instance")  # box-a has joined one: the instance checks run too
        os.makedirs(os.path.join(self.instance, "knowledge"))
        self.inv = {"machines": {
            "box-a": {"ssh": ["A"], "roles": ["chatlog", "dcattach"]},
            "box-b": {"ssh": ["B"], "roles": ["chatlog", "progress"]},
            "gone": {"ssh": ["G"], "roles": ["chatlog"]},
        }, "instance": {"repo": "git@example.com:me/instance.git"}}

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def fake_run_on(self, machine, script, interpreter="python3", timeout=600):
        alias = machine["ssh"][0]
        home = {"A": self.homes["box-a"], "B": self.homes["空白 box b"]}.get(alias)
        if home is None:
            return None, 255, "", "ssh: connect to host G: No route to host"
        env = dict(os.environ, HOME=home, ALT_INSTANCE=self.instance if alias == "A" else "")
        with open(os.path.join(self.instance, "machines.json"), "w") as f:
            json.dump(self.inv, f)
        r = subprocess.run([sys.executable, "-"], input=script, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, env=env, timeout=timeout)
        return alias, r.returncode, r.stdout, r.stderr

    def run_main(self, names=()):
        out = io.StringIO()
        with mock.patch.object(doctor, "inventory", return_value=json.loads(json.dumps(self.inv))), \
                mock.patch.object(doctor, "plugins_on", lambda n: placed(self.inv, n)), \
                mock.patch.object(doctor, "run_on", self.fake_run_on), \
                mock.patch.object(doctor, "local_head_status", return_value=("abc1234", "")), \
                contextlib.redirect_stdout(out):
            code = doctor.main(list(names))
        return code, out.getvalue()

    def test_every_machine_gets_a_row_and_problems_are_listed(self):
        code, text = self.run_main()
        rows = [l for l in text.splitlines() if l.split()[:1] and l.split()[0] in self.inv["machines"]]
        self.assertEqual([r.split()[0] for r in rows], ["box-a", "box-b", "gone"], text)
        self.assertEqual(code, 1, "empty homes have problems")
        self.assertIn("gone：連不上", text)
        self.assertIn("box-a：~/altention/engine/knowledge 不是指向實例的連結", text, "the engine-path check ran")

    def test_some_machines_only(self):
        code, text = self.run_main(["box-b"])
        self.assertIn("box-b", text)
        self.assertNotIn("gone", text)

    def test_a_broken_list_still_checks_this_machine(self):
        out = io.StringIO()
        with mock.patch.object(doctor, "inventory", side_effect=ValueError("bad json")), \
                mock.patch.object(doctor, "run_on", lambda m, s, **k: self.fake_run_on({"ssh": ["A"]}, s, **k)), \
                mock.patch.object(doctor, "local_head_status", return_value=("abc1234", "")), \
                contextlib.redirect_stdout(out):
            doctor.main([])
        self.assertIn("機器清單讀不出來", out.getvalue())
        self.assertIn("local", out.getvalue())



def placed(inv, name):
    """What plugin.json would put on `name` in these tests: the plugins its entry names among its roles; none unknown."""
    m = (inv.get("machines") or {}).get(name) or {}
    return set(m.get("roles", [])) & {"chatlog", "progress", "planner", "usage"}, set()


def healthy(**changes):
    """What the probe says on a machine with nothing wrong (every role's facts in place), with `changes` on top."""
    now = doctor.datetime.now(doctor.timezone.utc).isoformat(timespec="seconds")
    o = {"hostname": "host-a", "python": "3.8.10", "code": "abc1234", "cron_chatlog": True, "cron_progress": True,
         "cron_progress_paused": False, "hooks": True, "cleanup_days": 36500,
         "chatlog_last_sync": now, "chatlog_error": None, "progress_last_run": now, "progress_errors": 0, "progress_running": False,
         "global_md": True, "global_md_state": "composed", "bot_env": True, "bot_cron": True, "bot_supervisor": True,
         "bot_heartbeat_age": 3, "instance": "/home/alice/我的 實例", "stray_bot": False,
         "this_machine": "host-a", "sync": {"engine": {}, "instance": {}, "links": {"memory": "instance"}},
         "tmp_inodes": 5, "tmp_space": 10, "engine_paths": {"knowledge": "link", "memory": "link", "docs/refactor": "link"},
         "kept_twice": [], "global_edited": [], "adopted": [], "events_bad": 0, "open_dirs": [],
         "skills": ["council", "report", "tour"], "slept": 0.0}
    o.update(changes)
    return o


class Records(unittest.TestCase):
    """Every check is one record; what people read comes from the records (DESIGN.md 5.5)."""
    FIELDS = {"id", "machine", "status", "msg", "fix"}
    unset = {}  # machine: [(plugin, setting)] as unset_channels says
    off = {}  # machine: {skill} as skills_off says
    needs = {}  # machine: [(plugin, needed)] as unmet_needs says

    def run_doctor(self, probes, argv=(), inv=None, scan=None):
        """probes: {machine: probe dict, or None for a machine that cannot be reached}."""
        inv = inv or {"machines": {n: {"ssh": [n.upper()], "roles": ["chatlog", "progress", "dcbot"]} for n in probes}}

        def fake_run_on(machine, script, interpreter="python3", timeout=600):
            name = next(n for n, m in inv["machines"].items() if m["ssh"] == machine["ssh"])
            if probes[name] is None:
                return None, 255, "", "ssh: connect to host: No route to host"
            probe = dict(probes[name])
            if probe.get("this_machine") == "host-a":  # healthy()'s default: each machine knows its own name
                probe["this_machine"] = name
            return machine["ssh"][0], 0, "a line of noise first\n" + json.dumps(probe), ""
        out = io.StringIO()
        with mock.patch.object(doctor, "inventory", return_value=json.loads(json.dumps(inv))), \
                mock.patch.object(doctor, "run_on", fake_run_on), \
                mock.patch.object(doctor, "local_head_status", return_value=("abc1234", "")), \
                mock.patch.object(doctor, "plugins_on", lambda n: placed(inv, n)), \
                mock.patch.object(doctor, "unset_channels", lambda n: self.unset.get(n, [])), \
                mock.patch.object(doctor, "skills_off", lambda n: self.off.get(n, set())), \
                mock.patch.object(doctor, "unmet_needs", lambda n: self.needs.get(n, [])), \
                mock.patch.object(doctor, "privacy_record", return_value=scan), \
                contextlib.redirect_stdout(out):
            code = doctor.main(list(argv))
        return code, out.getvalue()

    def records(self, probes, **kw):
        code, text = self.run_doctor(probes, ["--json"], **kw)
        return code, json.loads(text)

    def test_an_engine_from_an_archive_is_no_missing_engine(self):
        code, recs = self.records({"host-a": healthy(code="archive"), "host-b": healthy(code="")})
        got = {(r["machine"], r["id"]): r for r in recs}
        self.assertEqual((got["host-a", "core.engine"]["status"], got["host-a", "core.engine"]["value"]), ("ok", "壓縮檔"))
        self.assertNotIn(("host-a", "core.engine-version"), got, "an archive has no commit to compare")
        self.assertEqual(got["host-b", "core.engine"]["status"], "fail")
        self.assertIn("讀不到", got["host-b", "core.engine"]["msg"], "a .git git cannot read is not an archive")
        _, text = self.run_doctor({"host-a": healthy(code="archive")})
        self.assertIn("壓縮檔", text.splitlines()[1])

    def test_this_engine_unreadable_compares_no_versions(self):
        with mock.patch.object(doctor, "local_head_status", return_value=("", "讀不到 git：dubious ownership")):
            recs = json.loads(self.run_doctor_plain({"host-a": healthy(code="abc1234"), "host-b": healthy(code="def5678")}))
        ids = [(r["machine"], r["id"], r["status"]) for r in recs]
        self.assertIn((None, "core.local-engine", "warn"), ids)
        self.assertFalse([i for i in ids if i[1] == "core.engine-version"])
        with mock.patch.object(doctor, "local_head_status", return_value=("", "")):  # this engine from an archive
            recs = json.loads(self.run_doctor_plain({"host-a": healthy(code="abc1234")}))
        self.assertFalse([r for r in recs if r["id"] in ("core.local-engine", "core.engine-version")])

    def run_doctor_plain(self, probes):
        """--json with local_head_status as the caller patched it."""
        inv = {"machines": {n: {"ssh": [n.upper()], "roles": ["chatlog", "progress", "dcbot"]} for n in probes}}
        out = io.StringIO()
        with mock.patch.object(doctor, "inventory", return_value=json.loads(json.dumps(inv))), \
                mock.patch.object(doctor, "plugins_on", lambda n: placed(inv, n)), \
                mock.patch.object(doctor, "run_on", lambda m, sc, **k: (m["ssh"][0], 0, json.dumps(
                    dict(probes[m["ssh"][0].lower()], this_machine=m["ssh"][0].lower())), "")), \
                mock.patch.object(doctor, "privacy_record", return_value=None), contextlib.redirect_stdout(out):
            doctor.main(["--json"])
        return out.getvalue()

    def test_the_probe_reads_a_clone_an_archive_and_nothing_with_a_space_in_the_path(self):
        home = tempfile.mkdtemp(prefix="家 目錄 ")
        try:
            eng = os.path.join(home, "my engine", "altention 引擎")
            def probe():
                r = subprocess.run([sys.executable, "-"], input="ENGINE = %r\n" % eng + doctor.PROBE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, universal_newlines=True, env=dict(os.environ, HOME=home), timeout=120)
                return json.loads(r.stdout.strip().splitlines()[-1])["code"]
            self.assertIsNone(probe(), "nothing there")
            os.makedirs(os.path.join(eng, "altention"))
            open(os.path.join(eng, "altention", "config.py"), "w").close()
            self.assertEqual(probe(), "archive")
            git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", eng]
            subprocess.run(git + ["init", "-q"], check=True)
            subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "x"], check=True)
            head = subprocess.run(git + ["rev-parse", "--short", "HEAD"], stdout=subprocess.PIPE, universal_newlines=True).stdout.strip()
            self.assertEqual(probe(), head, "the path with spaces reaches git whole")
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_the_probe_reads_how_progress_last_failed(self):
        """progress notes a round that stopped (state.json last_failed); the probe also takes cron.out's last line."""
        home = tempfile.mkdtemp(prefix="家 目錄 ")
        try:
            eng = os.path.join(home, "engine")
            os.makedirs(eng)
            cache = os.path.join(home, ".cache", "progress")
            os.makedirs(cache)

            def probe():
                r = subprocess.run([sys.executable, "-"], input="ENGINE = %r\n" % eng + doctor.PROBE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, universal_newlines=True, env=dict(os.environ, HOME=home), timeout=120)
                o = json.loads(r.stdout.strip().splitlines()[-1])
                return o.get("progress_failed"), o.get("progress_log_tail")
            self.assertEqual(probe(), (None, None), "nothing there")
            with open(os.path.join(cache, "state.json"), "w") as f:
                json.dump({"last_run": None, "last_failed": {"at": "2026-10-15T12:00:00+08:00", "why": "原因"}}, f)
            with open(os.path.join(cache, "cron.out"), "w") as f:
                f.write("x" * 9000 + "\nTraceback (most recent call last):\nSystemExit: 原因\n\n")
            self.assertEqual(probe(), ({"at": "2026-10-15T12:00:00+08:00", "why": "原因"}, "SystemExit: 原因"))
        finally:
            shutil.rmtree(home)

    def test_the_probe_reads_how_usage_last_collected(self):
        """usage/collect.py notes its rounds in ~/.cache/usage/state.json; its schedule's output is ~/.cache/usage.out."""
        home = tempfile.mkdtemp(prefix="家 目錄 ")
        try:
            eng = os.path.join(home, "engine")
            os.makedirs(eng)
            cache = os.path.join(home, ".cache", "usage")
            os.makedirs(cache)

            def probe():
                r = subprocess.run([sys.executable, "-"], input="ENGINE = %r\n" % eng + doctor.PROBE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, universal_newlines=True, env=dict(os.environ, HOME=home), timeout=120)
                o = json.loads(r.stdout.strip().splitlines()[-1])
                return [o.get(k) for k in ("usage_last_run", "usage_failed", "usage_errors", "usage_log_tail")]
            self.assertEqual(probe(), [None, None, [], None], "nothing there")
            with open(os.path.join(cache, "state.json"), "w") as f:
                json.dump({"last_run": "2026-10-15T11:07:00+08:00", "last_failed": {"at": "2026-10-15T12:07:00+08:00", "why": "原因"},
                           "last_errors": ["host-b：連不上"]}, f)
            with open(os.path.join(home, ".cache", "usage.out"), "w") as f:
                f.write("x" * 9000 + "\nusage：原因\n\n")
            self.assertEqual(probe(), ["2026-10-15T11:07:00+08:00", {"at": "2026-10-15T12:07:00+08:00", "why": "原因"},
                                       ["host-b：連不上"], "usage：原因"])
            share = os.path.join(home, ".local", "share", "usage")
            os.makedirs(share)
            os.chmod(share, 0o755)  # made before collect.py and usage/install.sh made it 700
            r = subprocess.run([sys.executable, "-"], input="ENGINE = %r\n" % eng + doctor.PROBE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, universal_newlines=True, env=dict(os.environ, HOME=home), timeout=120)
            self.assertIn("~/.local/share/usage", json.loads(r.stdout.strip().splitlines()[-1])["open_dirs"])
            os.makedirs(os.path.join(eng, "usage"))
            open(os.path.join(eng, "usage", "collect.py"), "w").close()
            os.utime(os.path.join(eng, "usage", "collect.py"), (1900000000, 1900000000))
            os.utime(os.path.join(home, ".cache", "usage.out"), (1800000000, 1800000000))
            r = subprocess.run([sys.executable, "-"], input="ENGINE = %r\n" % eng + doctor.PROBE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, universal_newlines=True, env=dict(os.environ, HOME=home), timeout=120)
            o = json.loads(r.stdout.strip().splitlines()[-1])
            self.assertEqual((o["usage_log_mtime"], o["usage_code_mtime"], o["progress_code_mtime"]), (1800000000, 1900000000, None))
            if sys.platform.startswith("linux"):  # how long it has been suspended since boot: 0 or more
                self.assertGreaterEqual(o["slept"], 0.0)
        finally:
            shutil.rmtree(home)

    def test_the_probe_reads_how_the_bots_last_run_ended(self):
        """support-4 dry run 2026-10-15: a wrong token is LoginFailure in supervisor.out every few seconds; the probe
        reads how the last run that ended ended, and how long ago a run going now began (bot.pid)."""
        home = tempfile.mkdtemp(prefix="家 目錄 ")
        try:
            eng = os.path.join(home, "engine")
            state = os.path.join(eng, "dcbot", "state")
            os.makedirs(state)
            open(os.path.join(eng, "dcbot", "state", "bot.pid"), "w").close()

            def probe(*lines):
                with open(os.path.join(state, "supervisor.out"), "w") as f:
                    f.write("".join(l + "\n" for l in lines))
                r = subprocess.run([sys.executable, "-"], input="ENGINE = %r\n" % eng + doctor.PROBE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, universal_newlines=True, env=dict(os.environ, HOME=home), timeout=120)
                o = json.loads(r.stdout.strip().splitlines()[-1])
                return o.get("bot_last_exit"), o.get("bot_started_age")
            start, gone = "2026-10-15 12:00:00 starting bot abc1234 (trial)", "2026-10-15 12:00:02 bot exited with 1 after 2s"
            wrong = ["Traceback (most recent call last):", "    raise LoginFailure('Improper token has been passed.') from exc",
                     "discord.errors.LoginFailure: Improper token has been passed."]
            self.assertEqual(probe(start, *wrong, gone), ("login", None))
            self.assertEqual(probe(start, *wrong, gone, start, *wrong, gone), ("login", None), "the same again and again")
            got = probe(start, *wrong, gone, start)
            self.assertEqual(got[0], "login", "the last run that ended")
            self.assertTrue(0 <= got[1] < 60, got)
            self.assertEqual(probe(start, "discord.errors.PrivilegedIntentsRequired: Shard ID None is requesting privileged "
                                   "intents", gone), ("intents", None))
            self.assertEqual(probe(start, *wrong, gone, start, "Traceback", "ValueError: x", gone), ("other", None),
                             "only the last run counts: a later run that ended otherwise is not the token")
            self.assertEqual(probe(start, "2026-10-15 12:02:00 version abc1234 is good")[0], None, "running, none ended")
            self.assertEqual(probe("discord.errors.LoginFailure: x", gone), (None, None), "no start seen: nothing said")
            quoted = ["2026-10-15 12:00:01 [enter] #dev: why does discord.errors.LoginFailure: show up?",
                      "  discord.errors.PrivilegedIntentsRequired: quoted in a reply", "Traceback", "ValueError: x"]
            self.assertEqual(probe(start, *quoted, gone), ("other", None), "the name quoted in the bot's own lines is not the exception")
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_a_wrong_token_on_the_only_machine_is_said_with_how_to_type_it_again(self):
        """support-4 dry run 2026-10-15: one machine, no list, a wrong token: doctor said all was well. The bot's
        settings put the bot on the only machine; with no heartbeat, the supervisor's LoginFailure says why."""
        single = {"machines": {"host-a": {"ssh": ["local"], "roles": []}}, "single": True}

        def run(**probe):
            code, recs = self.records({"host-a": healthy(**probe)}, inv=single)
            return code, {r["id"]: r for r in recs if r["machine"] == "host-a"}
        code, r = run(bot_heartbeat_age=None, bot_last_exit="login")
        self.assertEqual((code, r["dcbot.heartbeat"]["status"]), (1, "fail"))
        self.assertIn("token 不對", r["dcbot.heartbeat"]["msg"])
        self.assertIn("install.sh discord", r["dcbot.heartbeat"]["fix"])
        code, r = run(bot_heartbeat_age=900, bot_last_exit="login")
        self.assertIn("token 不對", r["dcbot.heartbeat"]["msg"], "a heartbeat left from an earlier token")
        code, r = run(bot_heartbeat_age=None, bot_last_exit="intents")
        self.assertIn("Message Content Intent", r["dcbot.heartbeat"]["msg"])
        code, r = run()  # online: a fresh heartbeat, whatever an earlier run said
        self.assertEqual((code, r["dcbot.heartbeat"]["status"]), (0, "ok"))
        code, r = run(bot_last_exit="login")
        self.assertEqual(r["dcbot.heartbeat"]["status"], "ok", "fixed since: the heartbeat is what counts")
        code, r = run(bot_heartbeat_age=None, bot_started_age=20, bot_last_exit="login")  # just started (again)
        self.assertEqual((code, r["dcbot.heartbeat"]["status"]), (0, "warn"))
        self.assertIn("剛啟動（20 秒前）", r["dcbot.heartbeat"]["msg"])
        code, r = run(bot_heartbeat_age=None, bot_started_age=200, bot_last_exit="login")
        self.assertEqual(r["dcbot.heartbeat"]["status"], "fail", "a run going past the time to log in")
        self.assertNotIn("token", r["dcbot.heartbeat"]["msg"], "the run going now is not the one that failed (token fixed, network slow)")
        self.assertIn("沒有心跳", r["dcbot.heartbeat"]["msg"])
        code, r = run(bot_heartbeat_age=None, bot_started_age=600, bot_last_exit=None)
        self.assertEqual(r["dcbot.heartbeat"]["status"], "fail", "past the time to log in")
        self.assertIn("從未啟動", r["dcbot.heartbeat"]["msg"])
        self.assertNotIn("token", r["dcbot.heartbeat"]["msg"], "nothing read says it is the token")
        code, r = run(bot_heartbeat_age=None, bot_last_exit="other")
        self.assertNotIn("token", r["dcbot.heartbeat"]["msg"])
        code, r = run(bot_heartbeat_age=None, bot_last_exit="banana", bot_started_age="x")
        self.assertEqual(r["dcbot.heartbeat"]["status"], "fail", "fields of another shape are not believed")
        self.assertNotIn("token", r["dcbot.heartbeat"]["msg"])
        code, r = run(bot_env=False, bot_heartbeat_age=None, bot_last_exit="login")
        self.assertNotIn("dcbot.heartbeat", r, "no settings here: no bot here")
        self.assertFalse([k for k, x in r.items() if k.startswith("dcbot.") and x["status"] != "ok"])
        listed = {"machines": {"host-a": {"ssh": ["local"], "roles": []}, "host-b": {"ssh": ["HOST-B"], "roles": ["dcbot"]}}}
        _, recs = self.records({"host-a": healthy(bot_heartbeat_age=None, bot_last_exit="login"), "host-b": healthy()}, inv=listed)
        self.assertFalse([x for x in recs if x["machine"] == "host-a" and x["id"] == "dcbot.heartbeat"],
                         "the list puts the bot elsewhere: believed (a stray setting is its own check)")

    def test_no_crontab_command_says_to_install_cron_not_to_run_apply(self):
        """support-4 dry run #2: without crontab, apply.py writes nothing; every fix says what install.sh's todo says."""
        import common
        _, recs = self.records({"host-a": healthy(crontab_missing=True, cron_chatlog=False, hooks=False)})
        got = {r["id"]: r for r in recs}
        self.assertEqual(got["core.crontab"]["status"], "fail")
        for check in ("core.crontab", "chatlog.cron", "chatlog.hooks"):
            self.assertIn(common.INSTALL_CRON, got[check]["fix"], check)
            self.assertNotIn("apply.py", got[check]["fix"], check)
        self.assertTrue(got["chatlog.cron"]["fix"].startswith("先處理「沒有 crontab 指令」那一項"), "a sentence of its own")
        _, recs = self.records({"host-a": healthy(crontab_missing=True, cron_progress=False, global_md=False)})
        reworded = [r for r in recs if (r.get("fix") or "").startswith("先處理")]
        self.assertTrue(reworded)
        for r in recs:
            self.assertNotIn("apply.py", r.get("fix") or "", r["id"])
        for r in reworded:
            self.assertEqual(r["fix"], "先處理「沒有 crontab 指令」那一項（在 host-a 上%s，再跑 ~/altention/engine/install.sh），"
                                       "再跑一次 doctor" % common.INSTALL_CRON, r["id"])
        _, recs = self.records({"host-a": healthy(cron_chatlog=False)})
        got = {r["id"]: r for r in recs}
        self.assertEqual(got["core.crontab"]["status"] if "core.crontab" in got else "ok", "ok")
        self.assertIn("apply.py", got["chatlog.cron"]["fix"], "with crontab there: apply.py as before")

    def test_a_cache_folder_open_to_others_is_a_reminder(self):
        """support-4 dry run #1: Claude Code's installer makes ~/.cache 755; a new user's doctor must not fail on it."""
        _, recs = self.records({"host-a": healthy(open_dirs=["~/.cache"])})
        got = {r["id"]: r for r in recs}
        self.assertEqual(got["core.private-dirs"]["status"], "ok")
        self.assertEqual(got["core.cache-open"]["status"], "warn")
        self.assertIn("chmod 700 ~/.cache", got["core.cache-open"]["fix"])
        _, recs = self.records({"host-a": healthy(open_dirs=["~/.cache", "/e/dcbot/state"])})
        got = {r["id"]: r for r in recs}
        self.assertEqual(got["core.private-dirs"]["status"], "fail")
        self.assertIn("/e/dcbot/state", got["core.private-dirs"]["msg"])
        self.assertNotIn("~/.cache", got["core.private-dirs"]["msg"])
        _, recs = self.records({"host-a": healthy()})
        self.assertNotIn("core.cache-open", [r["id"] for r in recs])

    def test_an_override_that_cannot_be_used_is_said_on_the_only_machine_too(self):
        """#code-review on c45394d: a typo in plugins.<name>.schedules went unsaid on a one-machine install (unknown is
        not listed there); the instance's overrides are said on every machine, with why."""
        now = 1791500000
        rep = {"format": 1, "at": now - 300, "add": [], "remove": [], "retime": [], "core": [], "refuse": None, "problems": [],
               "disabled": [], "unknown": [
                   ["progress", "x"],
                   ["progress/run", "實例的設定不能用：plugins.progress.schedules.run：every 7m 寫不成 cron；這一項現在的行不動"],
                   ["plugins.planner.schedules.remnd", "這一筆不用：planner 沒有叫 remnd 的排程（有：remind）"]]}
        for alone in (True, False):
            r = doctor.would_apply_record("host-a", rep, now, True, alone)
            self.assertEqual(r["status"], "warn", alone)
            self.assertIn("實例的排程設定有 2 筆不能用（apply 不動那幾行）：plugins.progress.schedules.run：every 7m 寫不成 cron；"
                          "plugins.planner.schedules.remnd：planner 沒有叫 remnd 的排程（有：remind）", r["msg"])
            self.assertEqual("不知道 1 項：progress" in r["msg"], not alone)

    def test_the_only_machine_does_not_list_plugins_placed_nowhere_yet(self):
        """Before the bot's token nothing places the bot's plugins: on the only machine the report does not say so
        (core.placements does); with more machines it still does."""
        now = 1791500000
        rep = {"format": 1, "at": now - 300, "add": [], "remove": [], "retime": [], "core": [], "refuse": None,
               "unknown": [["progress", "x"], ["usage", "x"]], "problems": [], "disabled": []}
        self.assertEqual(doctor.would_apply_record("host-a", rep, now, True, True)["status"], "ok")
        r = doctor.would_apply_record("host-a", rep, now, True, False)
        self.assertEqual(r["status"], "warn")
        self.assertIn("不知道 2 項：progress、usage", r["msg"])
        _, recs = self.records({"host-a": healthy(now=time.time(), apply_report=dict(rep, at=time.time() - 60))})
        self.assertEqual(next(r for r in recs if r["id"] == "plugins.would-apply")["status"], "ok", "one machine in the list")
        _, recs = self.records({"host-a": healthy(now=time.time(), apply_report=dict(rep, at=time.time() - 60)), "host-b": healthy()})
        self.assertEqual(next(r for r in recs if r["id"] == "plugins.would-apply" and r["machine"] == "host-a")["status"], "warn")

    def test_no_privacy_scan_without_the_instances_privacy_json(self):
        """support-4 dry run #1: a user who does not publish the engine has no privacy.json, and the scan would take a
        GitHub owner or an author's name for personal words."""
        inst = tempfile.mkdtemp(prefix="實例 ")
        try:
            with mock.patch.dict(os.environ, {"ALT_INSTANCE": inst}), \
                    mock.patch.object(doctor.subprocess, "run", side_effect=AssertionError("no scan")):
                self.assertIsNone(doctor.privacy_record())
            open(os.path.join(inst, "privacy.json"), "w").close()
            found = mock.Mock(stdout=json.dumps({"summary": {"personal": {"total": 3, "files": 1}}}))
            with mock.patch.dict(os.environ, {"ALT_INSTANCE": inst}), mock.patch.object(doctor.subprocess, "run", return_value=found):
                self.assertEqual(doctor.privacy_record()["status"], "warn")
        finally:
            shutil.rmtree(inst, ignore_errors=True)

    def test_the_probe_checks_docs_refactor_only_when_the_instance_has_it(self):
        home, inst = tempfile.mkdtemp(prefix="家 "), tempfile.mkdtemp(prefix="實例 ")
        try:
            with open(os.path.join(inst, "plugins.json"), "w") as f:
                f.write('{"format": 1}')

            def paths():
                r = subprocess.run([sys.executable, "-"], input="ENGINE = %r\n" % doctor.ENGINE_DIR + doctor.PROBE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, universal_newlines=True, timeout=120,
                                   env=dict(os.environ, HOME=home, ALT_INSTANCE=inst))
                return json.loads(r.stdout.strip().splitlines()[-1])["engine_paths"]
            self.assertEqual(sorted(paths()), ["knowledge", "memory"])
            os.makedirs(os.path.join(inst, "docs", "refactor"))
            self.assertEqual(sorted(paths()), ["docs/refactor", "knowledge", "memory"])
        finally:
            shutil.rmtree(home, ignore_errors=True)
            shutil.rmtree(inst, ignore_errors=True)

    def test_a_big_logs_file_warns_and_the_error_shows_its_end(self):
        """Manager 2026-10-07: a file past 50 MB in this machine's logs is a warning (GitHub refuses past 100 MB);
        a long chatlog error shows its end, where the reason is (LFS warnings come first)."""
        err = "push: " + "warning: LFS " * 40 + "remote: error: File x is 101 MB; exceeds GitHub's limit"
        _, recs = self.records({"host-a": healthy(chatlog_big=[["2026-10/x/raw.jsonl", 53 * 1048576]], chatlog_error=err)})
        big = next(r for r in recs if r["id"] == "chatlog.big-file")
        self.assertEqual(big["status"], "warn")
        self.assertIn("2026-10/x/raw.jsonl（53.0 MB）", big["msg"])
        self.assertIn("chatlog.py split", big["fix"])
        error = next(r for r in recs if r["id"] == "chatlog.error")
        self.assertIn("exceeds GitHub's limit", error["msg"])
        _, recs = self.records({"host-a": healthy()})
        self.assertEqual(next(r for r in recs if r["id"] == "chatlog.big-file")["status"], "ok")

    def test_unsure_segments_warn_and_name_the_folder(self):
        """Manager 2026-10-08: a conversation export leaves alone (segments, no complete list) shows in doctor."""
        _, recs = self.records({"host-a": healthy(chatlog_unsure=["2026-10/2026-10-03_x"])})
        r = next(r for r in recs if r["id"] == "chatlog.unsure-segments")
        self.assertEqual(r["status"], "warn")
        self.assertIn("2026-10/2026-10-03_x", r["msg"])
        self.assertIn("gotchas", r["fix"])
        _, recs = self.records({"host-a": healthy()})
        self.assertEqual(next(r for r in recs if r["id"] == "chatlog.unsure-segments")["status"], "ok")

    def test_what_apply_would_change_is_shown_from_each_machines_own_report(self):
        """3a.12, report only (manager 2026-10-09)."""
        now = 1791500000
        rep = {"format": 1, "at": now - 300, "add": [], "remove": [], "retime": [], "core": [], "refuse": None,
               "unknown": [], "problems": [], "disabled": []}
        item = {"plugin": "progress", "name": "run", "kind": "cron", "status": "missing"}

        def said(report, always_on=True, at=now):
            return doctor.would_apply_record("host-a", report, at, always_on)
        r = said(rep)
        self.assertEqual((r["status"], r["value"]), ("ok", "沒有要改的"))
        r = said(dict(rep, disabled=["usage"]))
        self.assertEqual(r["status"], "ok")
        self.assertIn("刻意停用：usage", r["value"])
        r = said(dict(rep, add=[item], retime=[dict(item, name="x")]))
        self.assertEqual(r["status"], "warn")
        self.assertIn("會補 1 項：progress/run", r["msg"])
        self.assertIn("只報告，沒有動手", r["msg"])
        r = said(dict(rep, core=[dict(item, plugin="core", name="sync")]))
        self.assertEqual(r["status"], "fail")
        self.assertIn("永遠不會自動改", r["msg"])
        r = said(dict(rep, remove=[item, item], refuse="mass-removal：會拿掉 2 項"))
        self.assertEqual(r["status"], "fail")
        self.assertIn("mass-removal", r["msg"])
        for old in ({}, None, dict(rep, at=now - 46 * 60), {"at": "x"}):
            r = said(old)
            self.assertEqual(r["status"], "warn", old)
            self.assertIn("沒有套用報告", r["msg"])
            self.assertNotEqual(r.get("value"), "沒有要改的", "no report is never read as nothing to change")
            r = said(old, always_on=False)
            self.assertEqual(r["status"], "ok", old)
            self.assertIn("不在線（最後一份報告", r["value"])
        r = said(dict(rep, add=[item]), always_on=False)
        self.assertEqual(r["status"], "warn", "a fresh report from a machine not always on is judged as any")
        r = said({"format": 1, "at": now - 60, "failed": "算不出來（結束碼 1）"})
        self.assertEqual(r["status"], "warn")
        self.assertIn("算不出來", r["msg"])
        r = said(dict(rep, unknown=[["odd", "x"]]))
        self.assertEqual(r["status"], "warn")
        self.assertIn("不知道 1 項：odd", r["msg"])
        for key in ("unknown", "problems", "disabled"):  # #code-review 2026-10-09: gone was "nothing to change"
            r = said({k: v for k, v in rep.items() if k != key})
            self.assertEqual(r["status"], "warn", key)
            self.assertIn("讀不懂", r["msg"])
        for at in (1e300, float("nan"), True, -1):
            r = said(dict(rep, at=at))
            self.assertEqual(r["status"], "warn", at)
            self.assertIn("沒有套用報告", r["msg"])
        for ahead, believed in ((60, True), (5 * 60, True), (6 * 60, False), (7200, False)):
            r = said(dict(rep, at=now + ahead))
            self.assertEqual(r["status"], "ok" if believed else "warn", ahead)
            if not believed:  # #code-review-2 2026-10-09: a report dated two hours ahead was taken for a fresh one
                self.assertIn("時間在未來", r["msg"])
                self.assertEqual(said(dict(rep, at=now + ahead), always_on=False)["status"], "warn", "not here does not explain it")
        for what, bad in (("format 2", dict(rep, format=2)), ("no format", {k: v for k, v in rep.items() if k != "format"}),
                          ("only format and at", {"format": 1, "at": now - 60}), ("add a string", dict(rep, add="progress/run")),
                          ("add of strings", dict(rep, add=["progress/run"])), ("core a number", dict(rep, core=3)),
                          ("remove an object", dict(rep, remove={})), ("unknown of numbers", dict(rep, unknown=[1])),
                          ("unknown a string", dict(rep, unknown="x")),
                          ("problems a number", dict(rep, problems=5)), ("disabled of numbers", dict(rep, disabled=[1])),
                          ("refuse a list", dict(rep, refuse=["x"]))):
            r = said(bad)
            self.assertEqual(r["status"], "warn", what)
            self.assertIn("讀不懂", r["msg"], what)
        _, recs = self.records({"host-a": healthy(apply_report=dict(rep, add=[{"plugin": 5, "name": ["x"], "installed": 7}]), now=now),
                                "host-b": healthy(apply_report=rep, now=now)})
        mine = [x for x in recs if x["id"] == "plugins.would-apply"]
        self.assertEqual(len(mine), 2, "whatever one machine's report holds, every machine still has its record")
        with mock.patch.object(doctor, "report_odd", side_effect=RuntimeError("boom")):
            _, recs = self.records({"host-a": healthy(apply_report=rep, now=now), "host-b": healthy(apply_report=rep, now=now)})
        mine = [x for x in recs if x["id"] == "plugins.would-apply"]
        self.assertEqual([x["status"] for x in mine], ["warn", "warn"])
        self.assertIn("讀不懂（RuntimeError: boom）", mine[0]["msg"])
        self.assertTrue([x for x in recs if x["id"] != "plugins.would-apply" and x["machine"] == "host-b"], "the other checks went on")
        _, recs = self.records({"host-a": healthy(apply_report=rep, now=now)})
        self.assertTrue([x for x in recs if x["id"] == "plugins.would-apply"])
        _, recs = self.records({"host-a": healthy()})
        self.assertFalse([x for x in recs if x["id"] == "plugins.would-apply"], "an engine that does not probe it: not shown")

    def test_one_machines_check_going_wrong_is_that_machines_one_record(self):
        """Manager 2026-10-09: every machine was checked in one map with nothing caught: one state file of another
        type than expected took the whole doctor, and doctor --watch, down."""
        now = 2000000000
        good = healthy(now=now, last_ok={"sync": now - 4 * 3600, "update": now}, alerts=[])
        probes = {"host-a": good, "host-b": ["not", "an", "object"], "host-c": healthy(now=now, last_ok={"sync": now, "update": now}),
                  "host-d": healthy(now=now, last_ok={"sync": now, "update": now})}
        _, recs = self.records(probes)
        whole = len([r for r in recs if r["machine"] == "host-d"])
        self.assertGreater(whole, 15)
        self.assertEqual(len([r for r in recs if r["machine"] == "host-c"]), whole, "the others' results are whole")
        self.assertGreater(len([r for r in recs if r["machine"] == "host-a"]), 15)
        broken = [r for r in recs if r["machine"] == "host-b"]
        self.assertEqual([(r["id"], r["status"]) for r in broken], [("core.check-error", "fail")])
        self.assertIn("檢查出錯：ValueError", broken[0]["msg"])
        with mock.patch.object(doctor, "would_apply_record", side_effect=RuntimeError("boom")), \
                mock.patch.object(doctor, "tidy", side_effect=lambda o: (_ for _ in ()).throw(KeyError("code")) if o.get("hostname") == "x" else (o, [])):
            _, recs = self.records({"host-a": healthy(hostname="x"), "host-b": healthy()})
        self.assertEqual([r["id"] for r in recs if r["machine"] == "host-a"], ["core.check-error"])
        self.assertGreater(len([r for r in recs if r["machine"] == "host-b"]), 15)
        told = []
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        from altention import update
        self.addCleanup(lambda: os.path.exists(os.path.join(update.state_dir(), "watch-check-error.json")) and os.remove(
            os.path.join(update.state_dir(), "watch-check-error.json")))
        with mock.patch.object(update, "alert", lambda *a, **k: told.append(a)):
            code, text = self.run_doctor(probes, ["--watch"])
        self.assertEqual(sorted(a[1].split("@")[0] for a in told), ["host-a/core.last-sync-ok", "host-b/core.check-error"],
                         "the watch still tells what the whole machines show, and that one could not be checked")

    def test_a_whole_round_of_the_watch_leaves_its_records_for_programs_on_this_machine(self):
        """What --json prints, kept after each whole round of --watch (the status boards read it: no second round of
        asking every machine). Written whole or not at all; never by a round of some machines, never from another
        checkout; and that it cannot be written stops no telling."""
        now = 2000000000
        probes = {"host-a": healthy(now=now, last_ok={"sync": now, "update": now}), "host-b": None}
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        from altention import update
        path = os.path.join(update.state_dir(), doctor.RECORDS)
        for extra in ("", ".tmp"):
            if os.path.exists(path + extra):
                os.remove(path + extra)
        self.addCleanup(lambda: [os.remove(path + e) for e in ("", ".tmp") if os.path.exists(path + e)])
        self.addCleanup(lambda: os.path.exists(os.path.join(update.state_dir(), "watch-check-error.json")) and os.remove(
            os.path.join(update.state_dir(), "watch-check-error.json")))
        _, want = self.records(probes)

        def watch(argv=("--watch",), kind=("ok", "")):
            told = []
            with mock.patch.object(update, "alert", lambda *a, **k: told.append(a)), \
                    mock.patch.object(update, "verdict", lambda: kind):
                code, text = self.run_doctor(probes, list(argv))
            return code, text, told
        code, text, told = watch(kind=("other", "a worktree"))
        self.assertFalse(os.path.exists(path), "another checkout writes nothing")
        code, text, told = watch(("--watch", "host-a"))
        self.assertFalse(os.path.exists(path), "a round of some machines would pass for all of them")
        before = time.time()
        code, text, told = watch()
        with open(path, encoding="utf-8") as f:
            kept = json.load(f)
        self.assertEqual(sorted(kept), ["at", "records"])
        self.assertTrue(before <= kept["at"] <= time.time())
        self.assertEqual(kept["records"], want, "the same records --json gives")
        self.assertTrue([r for r in kept["records"] if (r["id"], r["machine"], r["status"]) == ("core.reachable", "host-b", "fail")])
        self.assertFalse(os.path.exists(path + ".tmp"))
        self.assertTrue(told, "and the unreachable machine was told as before")
        with open(path, "w") as f:
            f.write("an older round")
        with mock.patch.object(doctor.os, "replace", side_effect=OSError("disk full")):
            code, text, told = watch()
        self.assertIn("這一輪的結果寫不下來（OSError: disk full）；通知照常", text)
        self.assertIn("看了 2 台", text, "the round went on to its end")
        with open(path) as f:
            self.assertEqual(f.read(), "an older round", "the file a reader has is never half written")
        self.assertFalse(doctor.keep_records(update.state_dir(), [{"id": {1, 2}}]), "what is not JSON: said, not raised")

    def test_a_machine_that_cannot_be_checked_is_told_when_it_begins_and_when_it_ends(self):
        """Manager 2026-10-09: once when it begins, not again while it lasts, once when it is over."""
        now = 2000000000
        fine = healthy(now=now, last_ok={"sync": now, "update": now})
        broken = {"host-a": fine, "host-b": ["not", "an", "object"]}
        whole = {"host-a": fine, "host-b": fine}
        told = []
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        from altention import update
        state = os.path.join(update.state_dir(), "watch-check-error.json")
        if os.path.exists(state):
            os.remove(state)
        self.addCleanup(lambda: os.path.exists(state) and os.remove(state))

        def watch(probes):
            told.clear()
            with mock.patch.object(update, "alert", lambda *a, **k: told.append(a)):
                code, text = self.run_doctor(probes, ["--watch"])
            return code, text, [(a[1].split("@")[0], a[2]) for a in told]
        code, text, said = watch(broken)
        self.assertEqual(code, 1)
        self.assertEqual([k for k, _ in said], ["host-b/core.check-error"])
        self.assertIn("檢查出錯", said[0][1])
        for _ in range(3):  # it lasts: every round's line names it, nobody is told again
            code, text, said = watch(broken)
            self.assertEqual((code, said), (1, []))
            self.assertIn("host-b core.check-error", text)
        code, text, said = watch({"host-a": fine})  # a round that did not look at it: still in that state
        self.assertEqual(said, [])
        code, text, said = watch(whole)
        self.assertEqual(code, 0)
        self.assertEqual([k for k, _ in said], ["host-b/core.check-error-over"])
        self.assertIn("又檢查得了", said[0][1])
        code, text, said = watch(whole)
        self.assertEqual(said, [], "its end is said once")
        for junk in ('{"host-b": 1e300}', '{"host-b": NaN}', '{"host-b": true}', '{"host-b": -5}', '{"host-b": "x"}', "[1]", "{"):
            with open(state, "w") as f:  # #code-review 2026-10-09: 1e300 or NaN there stopped every round of --watch
                f.write(junk)
            code, text, said = watch(whole)
            self.assertEqual((code, said), (0, []), junk)
        code, text, said = watch(broken)
        self.assertEqual([k for k, _ in said], ["host-b/core.check-error"], "the next time it begins is told again")
        keys = [a[1] for a in told]
        self.assertTrue(keys[0].startswith("host-b/core.check-error@"), "a kind of its own: never held back by the six hours")
        code, text, said = watch({"host-a": fine, "host-b": None})  # cannot be reached now: no longer a check gone wrong,
        self.assertEqual([k for k, _ in said], ["host-b/core.reachable"])  # and not "can be checked again" either
        code, text, said = watch(whole)
        self.assertEqual(said, [], "its state was dropped when it could not be reached: nothing more to say of it")
        code, text, said = watch(broken)
        self.assertEqual([k for k, _ in said], ["host-b/core.check-error"])

    def test_what_the_probe_says_oddly_is_shown_and_not_told(self):
        now = 2000000000
        told = []
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        from altention import update
        with mock.patch.object(update, "alert", lambda *a, **k: told.append(a)):
            code, text = self.run_doctor({"host-a": healthy(now=now, last_ok={"sync": now, "update": now}, chatlog_error=5)}, ["--watch"])
        self.assertEqual((code, told), (0, []))
        self.assertNotIn("core.probe-odd", doctor.WATCHED + doctor.WATCHED_WARN)

    def test_a_field_of_another_type_costs_that_field_not_the_check(self):
        """The list given to the manager 2026-10-09: what the probe reads from files on the machine."""
        now = 2000000000
        odd = {"chatlog_error": 5, "chatlog_last_sync": ["x"], "last_ok": {"sync": "yesterday", "update": None},
               "update_skipped": {"since": "x", "why": "鎖"}, "update_blocked": "blocked", "instance_held": {"since": [], "hash": 7},
               "alerts": [["2026", "sync"]], "sync": {"links": "x", "engine": 3, "instance": {"error": 5, "rescued": "x"}},
               "cron_logs": {"sync": 5}, "skills": "council", "unchecked": [1, 2], "fallbacks": {"update": "x", "sync": [1, 2, 3]},
               "chatlog_big": [["x"]], "chatlog_unsure": "x", "open_dirs": [1], "adopted": {}, "engine_paths": [1],
               "tmp_space": "91%", "cleanup_days": "30", "timezone": [], "schedule_record": {"line": True, "round": "x", "future": {}},
               "kept_twice": 1, "global_edited": "x", "events_bad": "3"}
        for key, value in list(odd.items()) + [("now", "noon")]:
            with self.subTest(field=key):
                _, recs = self.records({"host-a": healthy(**dict({"now": now}, **{key: value})), "host-b": healthy(now=now)})
                mine = [r for r in recs if r["machine"] == "host-a"]
                self.assertNotIn("core.check-error", [r["id"] for r in mine], key)
                self.assertGreater(len(mine), 15, key)
                said = next(r for r in mine if r["id"] == "core.probe-odd")
                self.assertEqual(said["status"], "warn", key)
                self.assertIn(key, said["msg"])
        _, recs = self.records({"host-a": healthy(now=now, **odd)})
        self.assertNotIn("core.check-error", [r["id"] for r in recs])
        _, recs = self.records({"host-a": healthy(now=now)})
        self.assertEqual(next(r for r in recs if r["id"] == "core.probe-odd")["status"], "ok")
        _, recs = self.records({"host-a": healthy(now=now, alerts=[["2026-10-09", "watch", "x", "the words"]], chatlog_error="x" * 200,
                                                  last_ok={"sync": now - 60, "update": now - 60})})
        self.assertEqual(next(r for r in recs if r["id"] == "core.probe-odd")["status"], "ok", "what is as expected is left as it is")
        self.assertIn("the words", next(r for r in recs if r["id"] == "core.alerts")["msg"])

    def test_typed_text_taken_for_a_paste_is_shown_for_a_week(self):
        """Manager 2026-10-09: a new Claude Code moving its paste threshold must be seen."""
        now = 2000000000

        def item(**probe):
            _, recs = self.records({"host-a": healthy(now=now, **probe)})
            return next((r for r in recs if r["id"] == "dcbot.typed-as-paste"), None)
        r = item(typed_as_paste={"at": now - 3600, "count": 4, "chars": 1900})
        self.assertEqual(r["status"], "warn")
        self.assertIn("累計 4 次", r["msg"])
        self.assertIn("TYPE_CHUNK", r["fix"])
        self.assertEqual(item(typed_as_paste={"at": now - 8 * 86400, "count": 4})["status"], "ok")
        self.assertEqual(item(typed_as_paste={})["status"], "ok", "never seen")
        for what in ({"at": "x", "count": []}, {"at": 1e30, "count": 1}, {"at": float("nan")}, {"at": True}, {"at": now + 7200, "count": 2},
                     {"count": 3}):  # #code-review-2 2026-10-09: 1e30 took the machine's whole check down; a broken one said ok
            r = item(typed_as_paste=what)
            self.assertEqual(r["status"], "warn", what)
            self.assertIn("讀不懂", r["msg"], what)
        _, recs = self.records({"host-a": healthy(now=now, typed_as_paste={"at": 1e30})})
        self.assertNotIn("core.check-error", [x["id"] for x in recs])
        self.assertEqual(item(typed_as_paste={"at": now + 120, "count": 1})["status"], "warn", "clocks a little apart: believed")
        self.assertIn("被 Claude Code 當成貼上", item(typed_as_paste={"at": now + 120, "count": 1})["msg"])
        self.assertIsNone(item(), "an engine that does not probe it")
        self.assertNotIn("dcbot.typed-as-paste", doctor.WATCHED + doctor.WATCHED_WARN)

    def one(self, check_id, now=2000000000, **probe):
        _, recs = self.records({"host-a": healthy(now=now, **probe)})
        return next((r for r in recs if r["id"] == check_id), None)

    def test_the_bots_three_notes_when_there_is_no_file_or_not_one_the_bot_wrote(self):
        """3b 2.6e. What the bot itself writes into these files, and what doctor then says, is kept in
        dcbot/tests/test_doctor_agrees.py with files written by the bot's own functions (manager 2026-10-14: no
        shape written by hand; 30b3a99 read two of them as bare lists, and so did its tests). Here only what no
        bot wrote: no such key (a probe of before this), no such file, and things that are not the bot's shape."""
        for check_id, key in (("dcbot.turn-ends-late", "turn_ends_late"), ("dcbot.channels-unreachable", "channels_unreachable"),
                              ("dcbot.panes-parked", "panes_parked")):
            self.assertIsNone(self.one(check_id), check_id)
            self.assertEqual(self.one(check_id, **{key: {}})["status"], "ok", "no such file: the probe says {}")
            self.assertNotIn(check_id, doctor.WATCHED + doctor.WATCHED_WARN)
            for what in ("x", [1], [{"channel": "42"}], 5, {"channels": "x"}, {"channels": [1]}, {"at": "x"}, {"at": 1e30, "count": 1},
                         {"at": True}, {"count": 3}):
                if key == "turn_ends_late" and isinstance(what, dict) and "channels" in what:
                    continue
                if key != "turn_ends_late" and isinstance(what, dict) and "channels" not in what:
                    continue
                r = self.one(check_id, **{key: what})
                self.assertEqual(r["status"], "warn", (check_id, what))
                self.assertIn("讀不懂", r["msg"], (check_id, what))

    def test_settings_the_bot_would_refuse_to_open_a_conversation_with(self):
        """Manager 2026-10-14: the settings file being JSON is not enough: a value the bot cannot use opens nothing.
        Read the way the bot reads it; the message names the key and the value."""
        valid = ["acceptEdits", "plan", "bypassPermissions"]
        self.assertIsNone(self.one("dcbot.open-settings"))
        for fine in ({"valid": valid, "permission": None, "model": None}, {"valid": valid, "permission": "plan", "model": "opus"},
                     {"valid": valid, "permission": "", "model": ""}, {"valid": valid, "model_error": "ConfigError: broken"}):
            self.assertEqual(self.one("dcbot.open-settings", open_settings=fine)["status"], "ok", fine)
        r = self.one("dcbot.open-settings", open_settings={"valid": valid, "permission": "everything", "model": None})
        self.assertEqual(r["status"], "fail")
        self.assertIn("dcbot.default_permission 是「everything」", r["msg"])
        self.assertIn("acceptEdits、plan、bypassPermissions", r["msg"])
        self.assertIn("plugins.json", r["fix"])
        for bad in (5, ["plan"], {"a": 1}, True):
            self.assertIn("dcbot.default_permission", self.one("dcbot.open-settings", open_settings={"valid": valid, "permission": bad})["msg"], bad)
        r = self.one("dcbot.open-settings", open_settings={"valid": valid, "permission": "plan", "model": 5})
        self.assertEqual(r["status"], "fail")
        self.assertIn("dcbot.default_model 是「5」", r["msg"])
        r = self.one("dcbot.open-settings", open_settings={"valid": valid, "permission_error": "ConfigError: not JSON"})
        self.assertEqual(r["status"], "fail")
        self.assertIn("ConfigError: not JSON", r["msg"])
        self.assertIn("讀不到接頭的權限模式清單", self.one("dcbot.open-settings", open_settings={"error": "ImportError: x"})["msg"])
        self.assertEqual(self.one("dcbot.open-settings", open_settings="x")["status"], "ok", "not what the probe writes: nothing to say")

    def test_the_new_items_are_the_bots_machine_only(self):
        probe = dict(turn_ends_late={"at": "x"}, channels_unreachable="x", panes_parked="x", open_settings={"valid": [], "permission": "x"})
        self.assertEqual(self.one("dcbot.panes-parked", **probe)["status"], "warn", "with the bot's role they are there")
        _, text = self.run_doctor({"host-a": healthy(now=2000000000, **probe)}, ["--json"],
                                  inv={"machines": {"host-a": {"ssh": ["HOST-A"], "roles": ["chatlog", "progress"]}}})
        ids = {r["id"] for r in json.loads(text)}
        self.assertFalse(ids & {"dcbot.turn-ends-late", "dcbot.channels-unreachable", "dcbot.panes-parked", "dcbot.open-settings"})

    def test_enter_on_a_box_that_differed_is_counted_by_the_week(self):
        """Manager 2026-10-09: in the bot's own sessions Enter is pressed even when the box is not what was typed;
        how often is shown, so that it can be tightened if it turns out to be someone typing there."""
        now = 2000000000

        def item(**probe):
            _, recs = self.records({"host-a": healthy(now=now, **probe)})
            return next((r for r in recs if r["id"] == "dcbot.enter-differs"), None)
        self.assertEqual((item(enter_differs={})["status"], item(enter_differs={})["value"]), ("ok", 0))
        r = item(enter_differs={"times": [now - 60, now - 3600]})
        self.assertEqual((r["status"], r["value"]), ("ok", 2))
        r = item(enter_differs={"times": [now - 60, now - 3600, now - 86400, now - 8 * 86400, 1e300, "x", True]})
        self.assertEqual((r["status"], r["value"]), ("warn", 3), "the last week's, and only what is a time")
        self.assertIn("這一週 3 次", r["msg"])
        self.assertEqual(item(enter_differs={"times": "x"})["status"], "ok")
        self.assertIsNone(item())
        self.assertNotIn("dcbot.enter-differs", doctor.WATCHED + doctor.WATCHED_WARN)

    def test_json_is_a_list_of_whole_records(self):
        inv = {"machines": {"host-a": {"ssh": ["A"], "roles": ["chatlog", "progress", "dcbot"]},
                            "主機 乙": {"ssh": ["B"], "roles": ["chatlog", "dcattach"]},
                            "host-c": {"ssh": ["C"], "roles": []}},
               "instance": {"repo": "git@example.com:alice/my-instance.git"}}
        code, recs = self.records({"host-a": healthy(), "主機 乙": healthy(cron_progress=False, bot_cron=False), "host-c": None}, inv=inv)
        self.assertEqual(code, 1)
        for r in recs:
            self.assertEqual(set(r) - {"value"}, self.FIELDS, r)
            self.assertIn(r["status"], ("ok", "warn", "fail", "off"), r)
            self.assertRegex(r["id"], r"^[a-z]+(\.[a-z0-9-]+)+$", r)
            self.assertEqual(bool(r["fix"]), r["status"] != "ok", "a fix for every problem, none for what is fine: %r" % r)
            self.assertEqual(bool(r["msg"]), r["status"] != "ok", r)
        fails = [(r["machine"], r["id"]) for r in recs if r["status"] == "fail"]
        self.assertEqual(fails, [("host-c", "core.reachable")])
        self.assertIn("ssh C", next(r["fix"] for r in recs if r["machine"] == "host-c"))
        ids = {(r["machine"], r["id"]) for r in recs}
        self.assertIn(("host-a", "dcbot.heartbeat"), ids)
        self.assertIn(("host-a", "progress.last-run"), ids)
        self.assertNotIn(("主機 乙", "progress.cron"), ids, "no record for a check that does not apply to the machine")
        self.assertIn((None, "progress.single"), ids)

    def test_the_table_and_the_problems_come_from_the_records(self):
        recs = [doctor.record("core.machine-list", None, "ok", "", value="list"),
                doctor.record("core.reachable", "host-a", "ok", "", value="A"),
                doctor.record("core.engine", "host-a", "ok", "", value="abc1234"),
                doctor.record("progress.last-run", "host-a", "ok", "", value="running"),
                doctor.record("core.reachable", "主機 乙", "fail", "連不上（timeout）", "確認 主機 乙 開著"),
                doctor.record("progress.single", None, "fail", "進度同時排程在 host-a, 主機 乙，只能有一台", "只留一台"),
                doctor.record("core.privacy-scan", None, "warn", "個資掃描：引擎已提交的樹裡有 2 筆、1 個檔案", "看明細", 2)]
        text = doctor.render(recs)
        rows = {l.split()[0]: l for l in text.splitlines() if l.split()[:1]}
        self.assertEqual(rows["host-a"].split()[1:], ["A", "abc1234", "—", "執行中", "—", "正常"])
        self.assertIn("有 1 個問題", text.splitlines()[2])
        self.assertIn("×", text.splitlines()[2], "no alias for a machine that was not reached")
        self.assertIn("\n個資掃描：引擎已提交的樹裡有 2 筆、1 個檔案\n", text)
        self.assertTrue(text.endswith("問題：\n- 主機 乙：連不上（timeout）\n- 全體：進度同時排程在 host-a, 主機 乙，只能有一台"), text)
        self.assertNotIn("修：", text)
        self.assertIn("- 主機 乙：連不上（timeout）\n  修：確認 主機 乙 開著\n", doctor.render(recs, fixes=True))

    def test_last_ok_of_sync_and_update_and_the_alerts(self):
        """3a.10: each machine's last good round of either (45 minutes is three rounds), what it wrote down to tell."""
        now = 2000000000
        fine = healthy(now=now, last_ok={"sync": now - 600, "update": now - 300}, alerts=[])
        code, recs = self.records({"host-a": fine})
        by = {r["id"]: r for r in recs if r["machine"] == "host-a"}
        self.assertEqual((by["core.last-sync-ok"]["status"], by["core.last-update-ok"]["status"]), ("ok", "ok"))
        self.assertNotIn("core.alerts", by)
        stale = healthy(now=now, last_ok={"sync": now - 3 * 3600, "update": None},
                        alerts=[["2026-10-06T22:00:00+0800", "update", "check", "上游沒通過檢查"]])
        code, recs = self.records({"host-a": stale})
        by = {r["id"]: r for r in recs if r["machine"] == "host-a"}
        self.assertEqual(code, 1)
        self.assertEqual(by["core.last-sync-ok"]["status"], "fail")
        self.assertIn("180 分鐘", by["core.last-sync-ok"]["msg"])
        self.assertEqual(by["core.last-update-ok"]["status"], "off", "not enabled yet is not a fault")
        self.assertEqual(by["core.alerts"]["status"], "warn")
        self.assertIn("上游沒通過檢查", by["core.alerts"]["msg"])

    def test_engine_commits_not_on_the_upstream_are_shown_not_a_problem(self):
        """C-31: the sync never pushes the engine; commits left here are said, with how to push them by hand."""
        def ahead(value):
            code, recs = self.records({"host-a": healthy(sync={"engine": {"ok": True, "error": None, "ahead": value},
                                                                    "instance": {}, "links": {"memory": "instance"}})})
            return code, {r["id"]: r for r in recs if r["machine"] == "host-a"}
        code, by = ahead(2)
        self.assertEqual(code, 0)
        self.assertEqual((by["sync.engine-ahead"]["status"], by["sync.engine-ahead"]["value"]), ("warn", 2))
        self.assertIn("2 個", by["sync.engine-ahead"]["msg"])
        self.assertIn("home-git push", by["sync.engine-ahead"]["fix"])
        self.assertIn("log @{u}..", by["sync.engine-ahead"]["fix"])
        self.assertEqual(by["sync.engine"]["status"], "ok")
        for value in (0, None, True, 1e300, "3"):  # level, an older sync.json, hand-edited
            code, by = ahead(value)
            self.assertNotIn("sync.engine-ahead", by, value)

    def test_packages_not_installed_after_a_move_are_a_fault_with_the_fix(self):
        """C-08: deps-pending as altention/update.py's mark_deps writes it ({head, why, at})."""
        def by(dp):
            code, recs = self.records({"host-a": healthy(deps_pending=dp)})
            return code, {r["id"]: r for r in recs if r["machine"] == "host-a"}["core.deps-pending"]
        code, r = by({"head": "0123456789abcdef", "why": "pip install 失敗（結束碼 1）：no network", "at": 1})
        self.assertEqual(r["status"], "fail")
        self.assertIn("0123456789ab", r["msg"])
        self.assertIn("no network", r["msg"])
        self.assertIn("機器人還在跑舊版", r["msg"])
        self.assertIn("dcbot/install.sh", r["fix"])
        self.assertIn("!restart", r["fix"])
        code, r = by(None)
        self.assertEqual(r["status"], "ok")
        code, r = by({})
        self.assertEqual(r["status"], "ok", "an empty record says nothing")

    def test_rollbacks_kept_on_a_machine_on_releases_are_not_to_push(self):
        """#bot-dev on fd23d9e (C-07): the supervisor's notice says the rollback stays here; doctor must not say to
        push it. The sync's engine record has "rollbacks" (altention/tests/test_update.py makes it with the sync)."""
        def by(engine):
            code, recs = self.records({"host-a": healthy(sync={"engine": dict({"ok": True, "error": None}, **engine),
                                                                    "instance": {}, "links": {"memory": "instance"}})})
            return code, {r["id"]: r for r in recs if r["machine"] == "host-a"}["sync.engine-ahead"]
        code, r = by({"ahead": 1, "rollbacks": 1})
        self.assertEqual((code, r["status"], r["fix"]), (0, "ok", ""))
        self.assertIn("不用推", r["value"])
        self.assertNotIn("home-git push", r["value"])
        code, r = by({"ahead": 2, "rollbacks": 1})
        self.assertEqual((r["status"], r["value"]), ("warn", 2))
        self.assertIn("home-git push", r["fix"], "the owner's own commit: as before")
        self.assertIn("其中 1 個是 supervisor 的退回 commit", r["fix"])
        self.assertIn("不要推它們", r["fix"])
        self.assertIn("請人看 git log", r["fix"])
        self.assertNotIn("只推自己的", r["fix"], "#code-review of 239abcc: not possible with a rollback under them")
        code, r = by({"ahead": 1, "rollbacks": 0, "rollbacks_unsure": 1})
        self.assertEqual((r["status"], r["value"]), ("warn", 1), "#code-review-2 of 7daa3df: an amended rollback")
        self.assertTrue(r["fix"].startswith("不要直接推"), r["fix"])
        self.assertIn("amend", r["fix"])
        self.assertIn("等於把退回推上上游", r["fix"])
        self.assertNotIn("要推的話", r["fix"])
        for odd in (None, True, -1, "1", 1.0):  # an older sync.json, hand-edited: as before
            code, r = by({"ahead": 1, "rollbacks": odd, "rollbacks_unsure": odd})
            self.assertEqual(r["status"], "warn", odd)
            self.assertNotIn("退回", r["fix"], odd)

    def test_a_progress_round_that_keeps_failing_says_why(self):
        """Owner's re-install 2026-10-15: every round stopped on the logs' place and doctor said only "not run yet"
        and all well. A failure newer than the last finished round is a problem with its line; an older one is not."""
        def iso(minutes_ago):
            return (doctor.datetime.now(doctor.timezone.utc) - doctor.timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")

        def check(**probe):
            code, recs = self.records({"host-a": healthy(**probe)})
            ids = [r["id"] for r in recs if r["machine"] == "host-a"]  # a list: a check said twice is not folded away
            self.assertEqual(ids.count("progress.last-run"), 1, ids)
            self.assertEqual(ids.count("chatlog.last-sync"), 1, "chatlog's own check stays (review of 9876add)")
            return code, [r for r in recs if r["machine"] == "host-a" and r["id"] == "progress.last-run"][0]

        why = "這台沒有對話紀錄（/x 不是對話紀錄的資料夾），也沒有設對話紀錄的遠端 repo。"
        code, r = check(progress_last_run=None, progress_failed={"at": iso(3), "why": why})
        self.assertEqual((code, r["status"]), (1, "fail"))
        self.assertIn("進度最近一次執行失敗（3 分鐘前）：" + why, r["msg"])
        self.assertIn("cron.out", r["fix"])
        code, r = check(progress_last_run=iso(30), progress_failed={"at": iso(2), "why": why})
        self.assertEqual(r["status"], "fail", "failing since it last finished")
        code, r = check(progress_last_run=iso(2), progress_failed={"at": iso(30), "why": why})
        self.assertEqual(r["status"], "ok", "a round finished after it: that one is over")
        code, r = check(progress_last_run=None, progress_failed=None, progress_log_tail="ImportError: no module x")
        self.assertEqual(r["status"], "fail", "stopped before it could note it: the output's last line")
        self.assertIn("輸出的最後一行：ImportError: no module x", r["msg"])
        code, r = check(progress_last_run=None, progress_failed=None, progress_log_tail="x", progress_running=True)
        self.assertNotIn("輸出的最後一行", r["msg"], "a round going now: not judged on the last one's words")
        stopped = doctor.datetime.now(doctor.timezone.utc) - doctor.timedelta(hours=3)
        code, recs = self.records({"host-a": healthy(progress_last_run=None, progress_failed={"at": iso(3), "why": why},
                                                     chatlog_last_sync=stopped.isoformat(timespec="seconds"))})
        got = [r for r in recs if r["machine"] == "host-a" and r["id"] == "chatlog.last-sync"]
        self.assertEqual([r["status"] for r in got], ["fail"], "chatlog stopped is still said while progress fails")

    def test_usage_that_never_collected_or_keeps_failing_says_why(self):
        """Owner's re-install 2026-10-15: every hourly round stopped (no plugins.usage.sources) and doctor said all was
        well. A failure newer than the last good round is a problem with its line; never a good one, the output's last
        line; a good one long ago, a problem; the last good round's time in the table."""
        def iso(minutes_ago):
            return (doctor.datetime.now(doctor.timezone.utc) - doctor.timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
        inv = {"machines": {"host-a": {"ssh": ["HOST-A"], "roles": ["chatlog", "progress", "dcbot", "usage"]}}}

        def check(**probe):
            code, recs = self.records({"host-a": healthy(**probe)}, inv=inv)
            ids = [r["id"] for r in recs if r["machine"] == "host-a"]
            self.assertLessEqual(ids.count("usage.last-run"), 1, ids)
            by = {r["id"]: r for r in recs if r["machine"] == "host-a"}
            return code, by.get("usage.last-run"), by.get("usage.errors")

        why = "usage：實例的 plugins.json 沒有用量來源（plugins.usage.sources），這一輪不收集"
        code, r, _ = check(usage_last_run=None, usage_failed={"at": iso(3), "why": why})
        self.assertEqual((code, r["status"]), (1, "fail"))
        self.assertIn("用量統計最近一次收集失敗（3 分鐘前）：" + why, r["msg"])
        self.assertIn("usage.out", r["fix"])
        code, r, _ = check(usage_last_run=iso(70), usage_failed={"at": iso(10), "why": why})
        self.assertEqual(r["status"], "fail", "failing since it last worked")
        code, r, _ = check(usage_last_run=iso(10), usage_failed={"at": iso(70), "why": why})
        self.assertEqual((r["status"], r["value"]), ("ok", iso(10)), "a good round after it")
        code, r, _ = check(usage_last_run=None, usage_failed=None, usage_log_tail="usage：沒有 ccusage")
        self.assertEqual(r["status"], "fail")
        self.assertIn("還沒有收集成功過，輸出的最後一行：usage：沒有 ccusage", r["msg"])
        code, r, _ = check(usage_last_run=None, usage_failed=None, usage_log_tail=None)
        self.assertEqual(r["status"], "warn", "nothing said anywhere yet: shown, not a fault")
        code, r, _ = check(usage_last_run=iso(5 * 60))
        self.assertEqual(r["status"], "fail")
        self.assertIn("用量統計上次收集：5 小時前", r["msg"])
        code, r, e = check(usage_last_run=iso(10), usage_errors=["host-b：連不上（經 B）"])
        self.assertEqual((r["status"], e["status"]), ("ok", "warn"))
        self.assertIn("1 台讀不到：host-b：連不上（經 B）", e["msg"])
        code, r, _ = check(usage_last_run=iso(10), usage_failed={"at": 5, "why": ["x"]})
        self.assertEqual(r["status"], "ok", "a note of another shape is not a failure")
        _, text = self.run_doctor({"host-a": healthy(usage_last_run=iso(30))}, inv=inv)
        self.assertIn("用量", text.splitlines()[0])
        self.assertIn("30 分鐘前", text.splitlines()[1])
        self.unset = {"host-a": [("usage", "usage.channel", "board")]}
        try:
            code, r, _ = check(usage_last_run=None, usage_failed=None, usage_log_tail="usage：實例設定沒有 usage.channel")
            self.assertIsNone(r, "no channel: its own note says why, not twice")
        finally:
            self.unset = {}
        code, recs = self.records({"host-a": healthy(usage_last_run=None, usage_log_tail="x")})
        self.assertNotIn("usage.last-run", [r["id"] for r in recs], "usage not on this machine")

    @mock.patch.object(doctor, "schedule_of", lambda *a: None)  # the fixed numbers (C-37: when no schedule is told)
    def test_an_output_older_than_the_code_or_the_line_is_what_ran_before(self):
        """Owner's update 2026-10-15: usage.out still said "no usage sources" from the old code, and doctor failed and
        told them to set what is no longer needed. An output older than the plugin's code here (an update) or its
        schedule line (cron_added) is not read; the new code's first turn is waited for, then a problem."""
        now = 2000000000
        inv = {"machines": {"host-a": {"ssh": ["HOST-A"], "roles": ["chatlog", "progress", "dcbot", "usage"]}}}
        old = "usage：實例的 plugins.json 沒有用量來源（plugins.usage.sources），這一輪不收集"

        def usage(added=None, **probe):
            base = dict(now=now, usage_last_run=None, usage_failed=None, usage_log_tail=old)
            base.update(probe)
            code, recs = self.records({"host-a": healthy(cron_added={"host-a": added} if added else {}, **base)}, inv=inv)
            return [r for r in recs if r["machine"] == "host-a" and r["id"] == "usage.last-run"][0]

        r = usage(usage_log_mtime=now - 5 * 3600, usage_code_mtime=now - 600)
        self.assertEqual(r["status"], "warn", r)
        self.assertIn("還沒輪到第一次收集（排程或程式 10 分鐘前裝上，60 分鐘內會跑）", r["msg"])
        self.assertNotIn("沒有用量來源", r["msg"])
        r = usage(added={"usage": now - 300}, usage_log_mtime=now - 3600, usage_code_mtime=now - 9 * 86400)
        self.assertEqual(r["status"], "warn", "older than the line apply just added")
        r = usage(usage_log_mtime=now - 60, usage_code_mtime=now - 600)
        self.assertEqual(r["status"], "fail", "written by the code there now: said")
        self.assertIn("輸出的最後一行：" + old, r["msg"])
        r = usage(usage_log_mtime=now - 5 * 3600, usage_code_mtime=now - 2 * 3600)
        self.assertEqual(r["status"], "fail", "past its first turn after the update with nothing new")
        self.assertIn("裝上 120 分鐘了，還沒有收集成功過", r["msg"])
        r = usage(added={"usage": now - 262 * 60}, usage_log_mtime=now - 3600, usage_code_mtime=now - 5 * 60)
        self.assertEqual(r["status"], "warn", "installed long ago, updated 5 minutes ago (owner's update): from the update")
        self.assertIn("排程或程式 5 分鐘前裝上，65 分鐘內會跑", r["msg"])
        r = usage(usage_log_mtime=None, usage_code_mtime=None)
        self.assertEqual(r["status"], "fail", "nothing to compare with: as before")
        code, recs = self.records({"host-a": healthy(now=now, progress_last_run=None, progress_log_tail="Traceback: old",
                                                     progress_log_mtime=now - 3600, progress_code_mtime=now - 60)})
        r = [r for r in recs if r["machine"] == "host-a" and r["id"] == "progress.last-run"][0]
        self.assertNotIn("Traceback: old", r["msg"], "progress's output goes the same way")
        code, recs = self.records({"host-a": healthy(now=now, progress_last_run=None, progress_log_tail="Traceback: new",
                                                     progress_log_mtime=now - 30, progress_code_mtime=now - 60)})
        r = [r for r in recs if r["machine"] == "host-a" and r["id"] == "progress.last-run"][0]
        self.assertIn("輸出的最後一行：Traceback: new", r["msg"])

    def test_a_skill_whose_plugin_is_off_is_not_missing(self):
        """council (or progress, report's owner) turned off on a machine: its skill is not there on purpose."""
        def got(skills):
            code, recs = self.records({"host-a": healthy(skills=skills)})
            return [r for r in recs if r["machine"] == "host-a" and r["id"] == "core.skills"][0]
        r = got(["report", "tour"])
        self.assertEqual(r["status"], "fail")
        self.assertIn("缺少 skill：council", r["msg"])
        self.off = {"host-a": {"council"}}
        try:
            self.assertEqual(got(["report", "tour"])["status"], "ok")
            self.assertIn("缺少 skill：report", got(["tour"])["msg"])
            self.assertNotIn("council", got(["tour"])["msg"])
            self.assertIn("缺少 skill：report, tour", got([])["msg"])  # the tour belongs to no plugin: never off
        finally:
            self.off = {}

    def test_a_skill_whose_plugin_cannot_be_placed_is_not_missing(self):
        """C-11: install.py makes no link for a plugin's skill whose place cannot be read (settings that cannot be
        read): doctor does not then say it is missing and send someone to apply; core.config says why."""
        inst = tempfile.mkdtemp(prefix="實例 ")
        try:
            with open(os.path.join(inst, "plugins.json"), "w") as f:
                f.write("{ not json")
            with mock.patch.dict(os.environ, {"ALT_INSTANCE": inst}):
                self.assertEqual(doctor.skills_off("host-a"), {"council", "report"})
            with open(os.path.join(inst, "plugins.json"), "w") as f:
                json.dump({"format": 1, "plugins": {"council": {"disabled_on": ["host-a"]}, "progress": {"on": ["host-a"]}}}, f)
            with mock.patch.dict(os.environ, {"ALT_INSTANCE": inst}):
                self.assertEqual(doctor.skills_off("host-a"), {"council"})
        finally:
            shutil.rmtree(inst, True)

    def test_the_skills_checked_are_those_of_that_machines_engine(self):
        """manager 2026-10-17: the list was fixed (council, report, tour); a new skill was never checked. The probe
        lists the folders under that machine's engine's skills/ (install.sh links each one) and which are linked."""
        home = tempfile.mkdtemp()
        try:
            engine = os.path.join(home, "altention", "engine")
            for n in ("council", "newone"):
                os.makedirs(os.path.join(engine, "skills", n))
            with open(os.path.join(engine, "skills", "README.md"), "w") as f:
                f.write("not a skill\n")
            os.makedirs(os.path.join(home, ".claude", "skills"))
            os.symlink(os.path.join(engine, "skills", "council"), os.path.join(home, ".claude", "skills", "council"))
            r = subprocess.run([sys.executable, "-"], input="ENGINE = %r\n" % "~/altention/engine" + doctor.PROBE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                               env=dict(os.environ, HOME=home), timeout=120)
            o = json.loads(r.stdout.strip().splitlines()[-1])
            self.assertEqual((o["skills_expected"], o["skills"]), (["council", "newone"], ["council"]))
        finally:
            shutil.rmtree(home, ignore_errors=True)

        def got(**probe):
            _, recs = self.records({"host-a": healthy(**probe)})
            return [r for r in recs if r["machine"] == "host-a" and r["id"] == "core.skills"][0]
        self.assertIn("缺少 skill：newone", got(skills=["council"], skills_expected=["council", "newone"])["msg"])
        self.assertEqual(got(skills=["council", "newone"], skills_expected=["council", "newone"])["status"], "ok")
        self.assertIn("缺少 skill：report, tour", got(skills=["council"])["msg"], "an older probe: this engine's skills/")
        self.assertIn("缺少 skill：report, tour", got(skills=["council"], skills_expected=7)["msg"], "not a list: the same")

    def test_skills_are_checked_on_a_machine_without_chatlog(self):
        """Every machine links the engine's skills, so a missing one is said wherever chatlog does not run too (the
        check used to sit in chatlog's part: #code-review-2 on 81ef970)."""
        inv = {"machines": {"host-a": {"ssh": ["HOST-A"], "roles": ["dcbot"]}}}
        code, recs = self.records({"host-a": healthy(skills=["council", "report"])}, inv=inv)
        r = [r for r in recs if r["machine"] == "host-a" and r["id"] == "core.skills"][0]
        self.assertEqual(r["status"], "fail")
        self.assertIn("缺少 skill：tour", r["msg"])
        self.assertIn("跑 altention apply host-a", r["fix"], "a broken link is apply's: the sync only adds missing ones")
        self.assertFalse([r for r in recs if r["id"] == "chatlog.skills"], "said once, under core")

    def test_a_secretary_still_in_another_folder_is_told_how_to_move(self):
        """Installed before #secretary (owner 2026-10-15): planner.channel names #chat, working in the instance. A note
        with how to move, never a change of what they set; nothing when it is the secretary's folder."""
        def got(sec):
            code, recs = self.records({"host-a": healthy(planner_secretary=sec)})
            return code, [r for r in recs if r["machine"] == "host-a" and r["id"] == "planner.secretary"]
        code, r = got({"cwd": "/home/alice/altention/instance", "folder": "/home/alice/altention/data/planner"})
        self.assertEqual([x["status"] for x in r], ["warn"])
        self.assertIn("工作目錄是 /home/alice/altention/instance，不是秘書自己的資料夾 /home/alice/altention/data/planner", r[0]["msg"])
        self.assertIn("remind.py --secretary", r[0]["fix"])
        self.assertIn("原本的頻道照舊", r[0]["fix"])
        code, r = got({"cwd": None, "folder": "/f"})
        self.assertIn("不是機器人管的 Claude 頻道", r[0]["msg"])
        self.assertEqual(got({"cwd": "/f", "folder": "/f"})[1], [])
        self.assertEqual(got(None)[1], [])

    def test_the_probe_reads_where_the_secretarys_channel_works(self):
        home = tempfile.mkdtemp(prefix="家 目錄 ")
        try:
            eng = os.path.join(home, "engine")
            state = os.path.join(eng, "dcbot", "state")
            os.makedirs(state)
            inst = os.path.join(home, "inst")
            os.makedirs(inst)
            with open(os.path.join(inst, "plugins.json"), "w") as f:
                json.dump({"format": 1, "planner": {"channel": "42"}}, f)
            with open(os.path.join(state, "channels.json"), "w") as f:
                json.dump({"42": {"cwd": inst, "configured": True}}, f)
            os.symlink(os.path.join(doctor.ENGINE_DIR, "altention"), os.path.join(eng, "altention"))

            def probe():
                r = subprocess.run([sys.executable, "-"], input="ENGINE = %r\n" % eng + doctor.PROBE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, universal_newlines=True, timeout=120,
                                   env=dict(os.environ, HOME=home, ALT_INSTANCE=inst, ALT_DATA=os.path.join(home, "data")))
                return json.loads(r.stdout.strip().splitlines()[-1]).get("planner_secretary")
            self.assertEqual(probe(), {"cwd": os.path.realpath(inst), "folder": os.path.realpath(os.path.join(home, "data", "planner"))})
            with open(os.path.join(inst, "plugins.json"), "w") as f:  # planner.folder: the owner's own folder (C-17: ~/life)
                json.dump({"format": 1, "planner": {"channel": "42", "folder": inst}}, f)
            self.assertEqual(probe(), {"cwd": os.path.realpath(inst), "folder": os.path.realpath(inst)})
            with open(os.path.join(state, "channels.json"), "w") as f:
                json.dump({}, f)
            self.assertEqual(probe()["cwd"], None)
        finally:
            shutil.rmtree(home)

    def test_one_item_with_two_lines_is_a_problem(self):
        """Owner 2026-10-15: after planner.channel moved, #chat's three lines stayed beside #secretary's: the briefing
        twice. Two lines with one item's marker, or a line of ours a plugin on here no longer asks for: fail."""
        inv = {"machines": {"host-a": {"ssh": ["HOST-A"], "roles": ["chatlog", "progress", "dcbot", "planner"]}}}

        def got(**probe):
            code, recs = self.records({"host-a": healthy(**probe)}, inv=inv)
            return [r for r in recs if r["machine"] == "host-a" and r["id"] == "plugins.cron-twice"][0]
        self.assertEqual(got()["status"], "ok")
        r = got(cron_named_twice=["planner/morning"])
        self.assertEqual(r["status"], "fail")
        self.assertIn("planner/morning（兩行以上）", r["msg"])
        self.assertIn("./install.sh", r["fix"])
        self.assertIn("crontab -l > ~/crontab.bak", r["fix"])
        old = {"status": "extra", "plugin": "planner", "name": None, "kind": "cron", "installed": "0 8 * * * x dc post 11 '⏰'  # altention:planner"}
        r = got(apply_report={"remove": [old]})
        self.assertEqual(r["status"], "fail")
        self.assertIn("planner 的舊行：0 8 * * * x dc post 11", r["msg"])
        self.assertEqual(got(apply_report={"remove": [dict(old, plugin="usage")]})["status"], "ok", "not on here: apply removes it")
        self.assertEqual(got(apply_report={"remove": [dict(old, status="elsewhere")]})["status"], "ok")

    def test_the_probe_counts_lines_by_their_item(self):
        home = tempfile.mkdtemp(prefix="家 目錄 ")
        try:
            eng = os.path.join(home, "engine")
            os.makedirs(eng)
            bindir = os.path.join(home, "bin")
            os.makedirs(bindir)
            table = os.path.join(home, "table")
            with open(table, "w") as f:
                f.write("0 8 * * * a dc post 11 x  # altention:planner/morning\n0 8 * * * a dc post 22 x  # altention:planner/morning\n"
                        "#0 9 * * * a  # altention:planner/evening\n0 9 * * * a  # altention:planner/evening\n"
                        "0 1 * * * b  # altention:usage\n0 2 * * * b  # altention:usage\n")
            with open(os.path.join(bindir, "crontab"), "w") as f:
                f.write("#!/bin/sh\ncat %s\n" % shlex_quote(table))
            os.chmod(os.path.join(bindir, "crontab"), 0o755)
            r = subprocess.run([sys.executable, "-"], input="ENGINE = %r\n" % eng + doctor.PROBE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, universal_newlines=True, timeout=120,
                               env=dict(os.environ, HOME=home, PATH=bindir + ":" + os.environ.get("PATH", "")))
            self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1])["cron_named_twice"], ["planner/morning"])
        finally:
            shutil.rmtree(home)

    def test_would_apply_says_kept_lines_are_kept(self):
        """#code-review-2 2026-10-16: an extra line apply leaves (its plugin on) was said as "會拿掉"."""
        kept = {"status": "extra", "plugin": "pm", "name": None, "kind": "cron", "installed": "*/10 * * * * x/pm.py  # altention:pm"}
        rep = {"format": 1, "add": [], "remove": [], "retime": [], "core": [], "unknown": [], "problems": [], "disabled": [],
               "kept": [kept], "at": 2000000000}
        r = doctor.would_apply_record("host-a", rep, 2000000000)
        self.assertEqual(r["status"], "warn")
        self.assertIn("留著", r["msg"])
        self.assertNotIn("會拿掉", r["msg"])
        self.assertEqual(doctor.report_odd(dict(rep, kept="x")), "kept 不是項目的清單")

    def test_the_entry_links_by_what_they_name(self):
        """C-17 S4: ~/.local/bin/altention naming the engine by an old name that is a link now resolves today and
        breaks once that name goes: the probe tells it apart from a link naming the engine itself."""
        home = tempfile.mkdtemp(prefix="家 entry ")
        try:
            eng = os.path.join(home, "engine")
            os.makedirs(os.path.join(eng, "bin"))
            open(os.path.join(eng, "bin", "altention"), "w").close()
            os.symlink(eng, os.path.join(home, "old name"))
            other = os.path.join(home, "other", "bin")
            os.makedirs(other)
            open(os.path.join(other, "altention"), "w").close()
            os.makedirs(os.path.join(home, "other", "altention"))
            open(os.path.join(home, "other", "altention", "config.py"), "w").close()  # another engine's checkout
            mine = os.path.join(home, "tools")
            os.makedirs(mine)
            open(os.path.join(mine, "altention"), "w").close()  # someone's own program called altention
            bindir = os.path.join(home, ".local", "bin")
            os.makedirs(bindir)

            def probe():
                r = subprocess.run([sys.executable, "-"], input="ENGINE = %r\n" % eng + doctor.PROBE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, universal_newlines=True, timeout=120, env=dict(os.environ, HOME=home))
                return json.loads(r.stdout.strip().splitlines()[-1])["entry_links"]
            self.assertEqual(probe(), {"altention": "missing", "alt": "missing"})
            os.symlink(os.path.join(eng, "bin", "altention"), os.path.join(bindir, "altention"))
            os.symlink(os.path.join(home, "old name", "bin", "altention"), os.path.join(bindir, "alt"))
            self.assertEqual(probe(), {"altention": "ok", "alt": "old-name"})
            os.remove(os.path.join(bindir, "altention"))
            os.symlink(os.path.join(other, "altention"), os.path.join(bindir, "altention"))
            os.remove(os.path.join(home, "old name"))
            self.assertEqual(probe(), {"altention": "elsewhere", "alt": "broken"})
            os.remove(os.path.join(bindir, "altention"))
            os.symlink(os.path.join(mine, "altention"), os.path.join(bindir, "altention"))
            self.assertEqual(probe()["altention"], "other", "a program called altention that is no engine's")
        finally:
            shutil.rmtree(home)

    def test_the_entry_record(self):
        def got(links):
            code, recs = self.records({"host-a": healthy(entry_links=links)})
            return [r for r in recs if r["machine"] == "host-a" and r["id"] == "core.entry"][0]
        self.assertEqual(got({"altention": "ok", "alt": "ok"})["status"], "ok")
        self.assertEqual(got({"altention": "ok", "alt": "other"})["status"], "ok", "someone else's alt: theirs")
        r = got({"altention": "old-name", "alt": "ok"})
        self.assertEqual(r["status"], "warn")
        self.assertIn("舊名字拿掉就斷", r["msg"])
        self.assertIn("bin/altention --link", r["fix"])
        for broken in ("missing", "broken", "elsewhere"):
            self.assertEqual(got({"altention": broken, "alt": "ok"})["status"], "fail", broken)
        self.assertEqual(got({"altention": "ok", "alt": "broken"})["status"], "warn")
        r = got({"altention": "other", "alt": "ok"})
        self.assertEqual(r["status"], "warn", "someone's own program called altention: left, said")
        self.assertIn("指到別的程式", r["msg"])
        code, recs = self.records({"host-a": healthy()})
        self.assertFalse([r for r in recs if r["id"] == "core.entry"], "an older probe without it: nothing said")

    @mock.patch.object(doctor, "schedule_of", lambda *a: None)  # the fixed numbers (C-37: when no schedule is told)
    def test_a_cron_line_added_a_moment_ago_waits_for_its_first_turn(self):
        """Owner's install 2026-10-15: progress, chatlog and the sync with no run yet right after install are a note
        while their line has not had its first turn (apply noted when it added it); past that, a problem; with no
        note (lines older than the note), as before."""
        now = 2000000000
        never = dict(now=now, progress_last_run=None, chatlog_last_sync=None, last_ok={"sync": None, "update": now})

        def by(added, always_on=True, cron_running=True, uptime=None, slept=0.0):
            entry = {"ssh": ["HOST-A"], "roles": ["chatlog", "progress", "dcbot"]}
            if always_on is not None:
                entry["always_on"] = always_on
            code, recs = self.records({"host-a": healthy(cron_added={"host-a": added} if added is not None else {},
                                                         cron_running=cron_running, uptime=uptime, slept=slept, **never)},
                                      inv={"machines": {"host-a": entry}})
            return code, {r["id"]: r for r in recs if r["machine"] == "host-a"}

        code, r = by({"core": now - 120, "chatlog": now - 120, "progress": now - 120})
        self.assertEqual(code, 0, [x for x in r.values() if x["status"] == "fail"])
        for check, left in (("progress.last-run", 13), ("chatlog.last-sync", 18), ("core.last-sync-ok", 18)):
            self.assertEqual(r[check]["status"], "warn", check)
            self.assertIn("排程 2 分鐘前裝上，%d 分鐘內會跑" % left, r[check]["msg"])
        code, r = by({"core": now - 10, "chatlog": now - 10, "progress": now - 10})
        self.assertIn("排程剛裝上，20 分鐘內會跑", r["core.last-sync-ok"]["msg"])
        self.assertIn("排程剛裝上，15 分鐘內會跑", r["progress.last-run"]["msg"])
        code, r = by({"core": now - 3600, "chatlog": now - 3600, "progress": now - 3600})
        self.assertEqual(code, 1)
        for check in ("progress.last-run", "chatlog.last-sync", "core.last-sync-ok"):
            self.assertEqual(r[check]["status"], "fail", check)
            self.assertIn("裝上 60 分鐘了", r[check]["msg"])
        # a laptop (always_on false), or the only machine with nothing saying it stays on that booted since: shown
        # only; the only machine on all along is not guessed asleep (owner's machine, 2026-10-15)
        for always_on, uptime, slept, said in ((False, None, 0.0, "這台不常開，開著一陣子後會跑"),
                                               (None, 1200, 0.0, "這台 20 分鐘前才開機，開著一陣子後會跑"),
                                               (None, 3 * 86400, 1800.0, "這台睡過，開著一陣子後會跑"),  # a laptop's uptime counts sleep
                                               (None, None, None, "看不出這台有沒有睡過")):  # macOS: a note for a day
            code, r = by({"core": now - 3600, "chatlog": now - 3600, "progress": now - 3600}, always_on, uptime=uptime, slept=slept)
            self.assertEqual(code, 0, always_on)
            for check in ("progress.last-run", "chatlog.last-sync", "core.last-sync-ok"):
                self.assertEqual(r[check]["status"], "warn", (always_on, check))
                self.assertIn("裝上 60 分鐘了", r[check]["msg"])
                self.assertIn(said, r[check]["msg"])
        for uptime in (None, 3 * 86400):  # never slept (Linux tells): a problem as before
            code, r = by({"core": now - 3600, "chatlog": now - 3600, "progress": now - 3600}, None, uptime=uptime, slept=10.0)
            self.assertEqual(code, 1, uptime)
            for check in ("progress.last-run", "chatlog.last-sync", "core.last-sync-ok"):
                self.assertEqual(r[check]["status"], "fail", (uptime, check))
                self.assertNotIn("不常開", r[check]["msg"])
        hour = {"core": now - 3600, "chatlog": now - 3600, "progress": now - 3600}
        for cron_running in (False, None):  # the only machine: relaxed only with a cron daemon seen (WSL; macOS)
            code, r = by(hour, None, cron_running)
            self.assertEqual(code, 1, cron_running)
            for check in ("progress.last-run", "chatlog.last-sync", "core.last-sync-ok"):
                self.assertEqual(r[check]["status"], "fail", (cron_running, check))
            self.assertEqual(r["core.cron-service"]["status"] if cron_running is False else None,
                             "fail" if cron_running is False else None)
        self.assertNotIn("core.cron-service", by(hour, None, None)[1], "not told where it cannot be told")
        import common
        svc = by(hour, False, False)[1]["core.cron-service"]
        self.assertEqual(svc["status"], "fail")
        self.assertIn(common.START_CRON, svc["fix"])
        self.assertNotIn("先裝 cron", svc["fix"])
        day = {"core": now - 25 * 3600, "chatlog": now - 25 * 3600, "progress": now - 25 * 3600}
        for always_on, slept in ((False, 0.0), (None, None), (None, 7200.0)):  # past a day with no run at all: a problem anywhere
            code, r = by(day, always_on, slept=slept)
            self.assertEqual(code, 1, always_on)
            for check in ("progress.last-run", "chatlog.last-sync", "core.last-sync-ok"):
                self.assertEqual(r[check]["status"], "fail", (always_on, check))
                self.assertIn("裝上 1500 分鐘了", r[check]["msg"])
        code, r = by(None)
        self.assertEqual((r["progress.last-run"]["status"], r["chatlog.last-sync"]["status"], r["core.last-sync-ok"]["status"]),
                         ("fail", "warn", "warn"), "no note: as before")
        code, r = by({"progress": now - 120})  # a note of another machine sharing the home is not this one's
        code2, recs = self.records({"host-a": healthy(cron_added={"host-b": {"progress": now - 120}}, **never)},
                                   inv={"machines": {"host-a": {"ssh": ["HOST-A"], "roles": ["progress"], "always_on": True}}})
        self.assertEqual(r["progress.last-run"]["status"], "warn")
        self.assertEqual(next(x for x in recs if x["id"] == "progress.last-run")["status"], "fail")
        code, r = by({"progress": "soon"})  # not a number: no note
        self.assertEqual(r["progress.last-run"]["status"], "fail")

    def test_a_board_channel_not_set_is_shown_with_how_to_set_it(self):
        """Owner's install 2026-10-15: status and usage skip every round without their channel; doctor says so."""
        self.unset = {"host-a": [("status", "status.channel", "board"), ("usage", "usage.channel", "board"),
                                 ("planner", "planner.channel", "claude")]}
        code, recs = self.records({"host-a": healthy()})
        by = {r["id"]: r for r in recs if r["machine"] == "host-a"}
        self.assertEqual(code, 0, "shown, not a problem")
        self.assertEqual(by["status.channel"]["status"], "warn")
        self.assertIn("看板頻道沒設定：status.channel", by["status.channel"]["msg"])
        self.assertIn('"status": {"channel": "<頻道 ID>"}', by["status.channel"]["fix"])
        self.assertIn('"usage": {"channel": "<頻道 ID>"}', by["usage.channel"]["fix"])
        self.assertIn("dcctl ensure dashboard", by["usage.channel"]["fix"])
        self.assertEqual(by["planner.channel"]["status"], "warn")
        self.assertIn("對話頻道沒設定：planner.channel", by["planner.channel"]["msg"])
        self.assertIn("--new-claude \"$(python3 planner/remind.py --secretary)\"", by["planner.channel"]["fix"])
        self.assertIn("#secretary", by["planner.channel"]["fix"])
        self.assertIn('"planner": {"channel": "<ID>"}', by["planner.channel"]["fix"])
        self.assertNotIn("dcctl ensure", by["planner.channel"]["fix"])
        code, text = self.run_doctor({"host-a": healthy()})
        self.assertIn("看板頻道沒設定：usage.channel", text)
        self.unset = {}

    def test_unset_channels_reads_plugin_json_and_the_setting(self):
        status = {"channels": [{"name": "dashboard", "kind": "board", "setting": "status.channel"}]}
        rows = [("status", "/e/status", status, "on", ""),
                ("usage", "/e/usage", {"channels": [{"name": "d", "kind": "board", "setting": "usage.channel"}]}, "off", ""),
                ("planner", "/e/planner", {"channels": [{"name": "s", "kind": "claude", "setting": "planner.channel"}]}, "on", ""),
                ("other", "/e/other", {"channels": [{"name": "n", "kind": "notes", "setting": "other.channel"}]}, "on", ""),
                ("odd", "/e/odd", {"channels": [{"name": "x"}, "y"]}, "on", ""), ("gone", "/e/gone", None, "unknown", "")]
        both = [("status", "status.channel", "board"), ("planner", "planner.channel", "claude")]
        for value, unset in ((None, both), ("", both), (True, both),
                             ("100000000000000020", []), (100000000000000020, [])):
            with mock.patch.object(doctor.altplugin, "on_this_machine", return_value=(rows, [])), \
                    mock.patch.object(doctor.altplugin.config, "setting", return_value=value):
                self.assertEqual(doctor.unset_channels("host-a"), unset, value)
        with mock.patch.object(doctor.altplugin, "on_this_machine", side_effect=RuntimeError("broken")):
            self.assertEqual(doctor.unset_channels("host-a"), [])

    def test_a_plugin_on_with_what_it_needs_turned_off_is_shown(self):
        """Manager 2026-10-15: progress on and chatlog off is a warn with both ways out; plainly from the settings."""
        self.needs = {"host-a": [("progress", "chatlog")]}
        code, recs = self.records({"host-a": healthy()})
        by = {r["id"]: r for r in recs if r["machine"] == "host-a"}
        self.assertEqual((code, by["progress.needs"]["status"]), (0, "warn"))
        self.assertIn("progress 開著，但它要用的 chatlog 在這台關掉了", by["progress.needs"]["msg"])
        self.assertIn("plugins.chatlog.disabled_on 拿掉 host-a", by["progress.needs"]["fix"])
        self.assertIn("python3 ops/apply.py host-a", by["progress.needs"]["fix"])
        self.needs = {}
        _, recs = self.records({"host-a": healthy()})
        self.assertFalse([r for r in recs if r["id"].endswith(".needs")])

    def test_unmet_needs_reads_requires_and_only_a_plain_off(self):
        progress = {"requires": [{"plugin": "chatlog", "data": "chatlog"}]}
        for chatlog, progress_at, want in (("disabled", "on", [("progress", "chatlog")]), ("on", "on", []),
                                           ("unknown", "on", []), ("off", "on", []), ("disabled", "off", []),
                                           ("disabled", "disabled", []), (None, "on", [])):
            rows = [("progress", "/e/progress", progress, progress_at, "")]
            if chatlog:
                rows.append(("chatlog", "/e/chatlog", {}, chatlog, ""))
            with mock.patch.object(doctor.altplugin, "on_this_machine", return_value=(rows, [])):
                self.assertEqual(doctor.unmet_needs("host-a"), want, (chatlog, progress_at))
        rows = [("odd", "/e/odd", {"requires": ["chatlog", {"x": 1}]}, "on", ""), ("gone", "/e/gone", None, "on", ""),
                ("chatlog", "/e/chatlog", {}, "disabled", "")]
        with mock.patch.object(doctor.altplugin, "on_this_machine", return_value=(rows, [])):
            self.assertEqual(doctor.unmet_needs("host-a"), [])
        with mock.patch.object(doctor.altplugin, "on_this_machine", side_effect=RuntimeError("broken")):
            self.assertEqual(doctor.unmet_needs("host-a"), [])

    def test_watch_tells_each_machine_whose_sync_stopped(self):
        now = 2000000000
        told = []
        sys_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        sys.path.insert(0, sys_path)
        from altention import update
        probes = {"host-a": healthy(now=now, last_ok={"sync": now - 4 * 3600, "update": now}, alerts=[]),
                  "host-b": healthy(now=now, last_ok={"sync": now - 60, "update": now}, alerts=[])}
        with mock.patch.object(update, "alert", lambda *a, **k: told.append(a)):
            code, text = self.run_doctor(probes, ["--watch"])
        self.assertEqual(code, 1)
        self.assertEqual([a[1] for a in told], ["host-a/core.last-sync-ok"])
        self.assertIn("看了 2 台：host-a core.last-sync-ok", text)
        with mock.patch.object(update, "alert", lambda *a, **k: None):
            code, text = self.run_doctor({"host-b": probes["host-b"]}, ["--watch"])
        self.assertIn("沒有要通知的", text, "a line even when there is nothing to tell")

    def test_watch_tells_a_conversation_export_leaves_alone(self):
        """Manager 2026-10-08: no backup of it from then on, so --watch tells it although it is a warning."""
        now = 2000000000
        told = []
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        from altention import update
        probes = {"host-a": healthy(now=now, last_ok={"sync": now, "update": now}, alerts=[], chatlog_unsure=["2026-10/x"]),
                  "host-b": healthy(now=now, last_ok={"sync": now, "update": now}, alerts=[])}
        with mock.patch.object(update, "alert", lambda *a, **k: told.append(a)):
            self.run_doctor(probes, ["--watch"])
        self.assertEqual([a[1] for a in told], ["host-a/chatlog.unsure-segments"])
        self.assertIn("2026-10/x", told[0][2])

    def test_watch_tells_when_update_stopped_on_an_always_on_machine(self):
        now = 2000000000
        told = []
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        from altention import update
        probes = {"host-a": healthy(now=now, last_ok={"sync": now, "update": now - 2 * 3600}, alerts=[]),
                  "host-b": healthy(now=now, last_ok={"sync": now, "update": None}, alerts=[])}
        with mock.patch.object(update, "alert", lambda *a, **k: told.append(a)):
            self.run_doctor(probes, ["--watch"])
        self.assertEqual([a[1] for a in told], ["host-a/core.last-update-ok"], "not enabled yet (off) is not told")

    def test_a_refused_upstream_is_a_fault_and_told(self):
        now = 2000000000
        blocked = {"hash": "abcdef1234567890", "why": "Python 語法檢查沒過", "since": now - 7200}
        code, recs = self.records({"host-a": healthy(now=now, last_ok={"sync": now, "update": now}, alerts=[],
                                                     update_blocked=blocked)})
        r = next(r for r in recs if r["id"] == "core.update-blocked")
        self.assertEqual(r["status"], "fail")
        self.assertIn("abcdef123456", r["msg"])
        self.assertIn("120 分鐘", r["msg"])
        self.assertIn("apply.py", r["fix"])
        code, recs = self.records({"host-a": healthy(now=now, last_ok={"sync": now, "update": now}, alerts=[], update_blocked=None)})
        self.assertEqual(next(r for r in recs if r["id"] == "core.update-blocked")["status"], "ok")

    def test_an_instance_upstream_held_back_is_a_fault_and_told(self):
        """Manager 2026-10-06: held by the settings check, the commits waiting here are counted; --watch tells it,
        for a machine not always on too (nothing about sleeping)."""
        now = 2000000000
        held = {"hash": "abcdef1234567890", "why": "machines.json 讀不出來", "since": now - 1800, "at": now, "ahead": "3"}
        sick = healthy(now=now, last_ok={"sync": now, "update": now}, alerts=[], instance_held=held)
        code, recs = self.records({"host-a": sick})
        r = next(r for r in recs if r["id"] == "core.instance-held")
        self.assertEqual(r["status"], "fail")
        for said in ("abcdef123456", "machines.json 讀不出來", "30 分鐘", "3 個 commit 還沒推出去"):
            self.assertIn(said, r["msg"])
        self.assertIn("apply.py host-a", r["fix"])
        told = []
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        from altention import update
        inv = {"machines": {"host-a": {"ssh": ["HOST-A"], "roles": ["chatlog"], "always_on": False}}}
        with mock.patch.object(update, "alert", lambda *a, **k: told.append(a)):
            self.run_doctor({"host-a": sick}, ["--watch"], inv=inv)
        self.assertEqual([a[1] for a in told], ["host-a/core.instance-held"])
        code, recs = self.records({"host-a": healthy(now=now, last_ok={"sync": now, "update": now}, alerts=[], instance_held={})})
        self.assertEqual(next(r for r in recs if r["id"] == "core.instance-held")["status"], "ok")

    def test_the_probe_reads_the_held_record_the_one_way(self):
        home = tempfile.mkdtemp()
        engine = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        try:
            state = os.path.join(home, ".local", "state", "altention")
            os.makedirs(state)
            with open(os.path.join(state, "instance-held"), "w") as f:
                json.dump({"hash": "abc", "why": "x", "since": 1, "at": 2, "ahead": "1"}, f)

            def probe():
                r = subprocess.run([sys.executable, "-"], input="ENGINE = %r\n" % engine + doctor.PROBE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                                   env=dict(os.environ, HOME=home), timeout=120)
                return json.loads(r.stdout.strip().splitlines()[-1])
            self.assertEqual(probe()["instance_held"]["hash"], "abc")
            with open(os.path.join(state, "instance-held"), "w") as f:
                f.write("{ half")
            self.assertEqual(probe()["instance_held"], {}, "unreadable: no record, as sync.instance_held reads it")
            with open(os.path.join(state, "instance-held"), "w") as f:
                json.dump({"hash": "abc", "why": "x", "since": "昨天", "at": 2, "ahead": "1"}, f)
            self.assertEqual(probe()["instance_held"], {}, "a time that is not a number: no record, no exception")
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_the_zone_and_the_record_only_scheduler_are_shown_never_told(self):
        """3a.11: the zone the schedules go by (a setting that cannot be used is a fault); the record-only round
        late is a warning on a machine always on, and nothing of it is told (manager 2026-10-07)."""
        now = 2000000000
        base = dict(now=now, last_ok={"sync": now, "update": now}, alerts=[])
        code, recs = self.records({"host-a": healthy(timezone={"name": "Asia/Taipei", "source": "setting", "problem": None},
                                                     schedule_record={"line": True, "round": now - 60}, **base)})
        by = {r["id"]: r for r in recs}
        self.assertEqual((by["core.timezone"]["status"], by["core.timezone"]["value"]), ("ok", "Asia/Taipei（實例設定）"))
        self.assertEqual(by["core.schedule-record"]["status"], "ok")
        sick = healthy(timezone={"name": None, "source": "setting", "problem": "core.timezone 不能用：Asia/Taipe 不認得"},
                       schedule_record={"line": True, "round": now - 3600}, **base)
        code, recs = self.records({"host-a": sick})
        by = {r["id"]: r for r in recs}
        self.assertEqual(by["core.timezone"]["status"], "fail")
        self.assertIn("Asia/Taipe", by["core.timezone"]["msg"])
        self.assertEqual(by["core.schedule-record"]["status"], "warn")
        self.assertIn("60 分鐘前", by["core.schedule-record"]["msg"])
        told = []
        from altention import update
        with mock.patch.object(update, "alert", lambda *a, **k: told.append(a)):
            self.run_doctor({"host-a": sick}, ["--watch"])
        self.assertEqual(told, [], "nothing of the scheduler is told while it only records")
        code, recs = self.records({"host-a": healthy(schedule_record={"line": True, "round": None, "future": now + 7200}, **base)})
        r = {r["id"]: r for r in recs}["core.schedule-record"]
        self.assertEqual(r["status"], "warn", "a mark in the future is not believed (#code-review-2 2026-10-07)")
        self.assertIn("時間在未來的記號，120 分鐘後，不採信", r["msg"])
        code, recs = self.records({"host-a": healthy(timezone={}, **base)})
        self.assertEqual({r["id"]: r for r in recs}["core.timezone"]["value"], "不知道（來源不明）")
        code, recs = self.records({"host-a": healthy(schedule_record={"line": False, "round": None}, **base)})
        self.assertNotIn("core.schedule-record", {r["id"] for r in recs}, "no line yet: nothing to say")

    def test_rounds_skipped_in_a_row_are_a_fault_after_45_minutes_and_told(self):
        now = 2000000000
        skip = {"since": now - 3600, "at": now, "why": "git 鎖被別人拿著"}
        code, recs = self.records({"host-a": healthy(now=now, last_ok={"sync": now, "update": now}, alerts=[], update_skipped=skip)})
        r = next(r for r in recs if r["id"] == "core.update-skipped")
        self.assertEqual(r["status"], "fail")
        self.assertIn("git 鎖被別人拿著", r["msg"])
        skip["since"] = now - 600
        code, recs = self.records({"host-a": healthy(now=now, last_ok={"sync": now, "update": now}, alerts=[], update_skipped=skip)})
        self.assertEqual(next(r for r in recs if r["id"] == "core.update-skipped")["status"], "ok")
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        from altention import update
        told = []
        skip["since"] = now - 3600
        with mock.patch.object(update, "alert", lambda *a, **k: told.append(a)):
            self.run_doctor({"host-a": healthy(now=now, last_ok={"sync": now, "update": now}, alerts=[], update_skipped=skip)}, ["--watch"])
        self.assertEqual([a[1] for a in told], ["host-a/core.update-skipped"])

    def test_a_newer_release_and_an_unpacked_engine_are_shown_never_told(self):
        """notify mode's newer release (update-available) and an engine that is not a git checkout: a warn each,
        with the way to update; --watch tells neither (manager 2026-10-14)."""
        now = 2000000000
        base = dict(now=now, last_ok={"sync": now, "update": now}, alerts=[])
        _, recs = self.records({"host-a": healthy(update_available={"tag": "v0.2.0", "commit": "abc", "at": now},
                                                  engine_unpacked=True, **base)})
        av = next(r for r in recs if r["id"] == "core.update-available")
        self.assertEqual(av["status"], "warn")
        self.assertIn("v0.2.0", av["msg"])
        self.assertIn("更新引擎", av["fix"])
        self.assertIn("altention/update.py --release", av["fix"], "the global instructions' way (C-18)")
        up = next(r for r in recs if r["id"] == "core.update-unpacked")
        self.assertEqual(up["status"], "warn")
        self.assertIn("不會自動更新", up["msg"])
        _, recs = self.records({"host-a": healthy(**base)})
        self.assertFalse({"core.update-available", "core.update-unpacked"} & {r["id"] for r in recs}, "nothing to say")
        _, recs = self.records({"host-a": healthy(update_available={"tag": 7}, engine_unpacked="yes", **base)})
        self.assertFalse({"core.update-available", "core.update-unpacked"} & {r["id"] for r in recs}, "odd fields: taken as not there")
        self.assertIn("core.probe-odd", {r["id"] for r in recs})
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        from altention import update
        told = []
        with mock.patch.object(update, "alert", lambda *a, **k: told.append(a)):
            self.run_doctor({"host-a": healthy(update_available={"tag": "v0.2.0", "commit": "abc", "at": now},
                                               engine_unpacked=True, **base)}, ["--watch"])
        self.assertEqual(told, [])

    def test_the_probe_reads_both_files(self):
        """The real PROBE: update-available and last_ok-update's checkout, as update.py writes them."""
        home = tempfile.mkdtemp()
        try:
            folder = os.path.join(home, ".local", "state", "altention")
            os.makedirs(folder)
            with open(os.path.join(folder, "update-available"), "w") as f:
                json.dump({"tag": "v0.2.0", "commit": "abc", "at": 1}, f)
            with open(os.path.join(folder, "last_ok-update"), "w") as f:
                json.dump({"at": 1, "note": "x", "checkout": False}, f)
            r = subprocess.run([sys.executable, "-"], input="ENGINE = %r\n" % os.path.join(home, "altention", "engine") + doctor.PROBE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                               env=dict(os.environ, HOME=home), timeout=120)
            o = json.loads(r.stdout.strip().splitlines()[-1])
            self.assertEqual(o["update_available"]["tag"], "v0.2.0")
            self.assertIs(o["engine_unpacked"], True)
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def probe(self, alerts_text):
        """The real PROBE, run in a throwaway home holding this alerts file."""
        home = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(home, ".local", "state", "altention"))
            with open(os.path.join(home, ".local", "state", "altention", "alerts"), "w") as f:
                f.write(alerts_text)
            r = subprocess.run([sys.executable, "-"], input="ENGINE = %r\n" % os.path.join(home, "altention", "engine") + doctor.PROBE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                               env=dict(os.environ, HOME=home), timeout=120)
            return json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_only_the_last_days_alerts_are_reported(self):
        """Manager 2026-10-16 (choice a): alerts from before the engine moved stayed on the dashboard for days. The
        probe takes the last 24 hours only (as its comment always said); the file is left as the record. A time that
        cannot be read counts as recent."""
        def line(ago, text):
            at = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(time.time() - ago))
            return "%s\tsync\tupdate-stale\t%s\n" % (at, text)
        o = self.probe(line(2 * 86400, "old one") + line(25 * 3600, "a day and an hour") + line(23 * 3600, "recent one")
                       + "not-a-time\tsync\tx\tno time\n")
        self.assertEqual([r[3] for r in o["alerts"]], ["recent one", "no time"])
        self.assertEqual(self.probe(line(2 * 86400, "old one"))["alerts"], [], "nothing to report")
        now = time.time()
        _, recs = self.records({"host-a": healthy(now=now, last_ok={"sync": now, "update": now}, alerts=[])})
        self.assertNotIn("core.alerts", [r["id"] for r in recs if r["status"] != "ok"])

    def test_something_pulled_in_unchecked_is_told_until_update_is_back(self):
        """Manager 2026-10-07: the sync's stand-in or a launcher's fallback pulled a commit stacked on a refused
        hash while update.py was not checking: a fail that --watch tells, gone once update.py's first round back
        wrote took-refused."""
        took = "2026-10-07T13:00:00+0800\tsync\tstacked-on-refused\tupdate 沒在檢查，拉進了疊在被擋的 aaa 上面的 bbb，沒有檢查\n"
        back = "2026-10-07T13:15:00+0800\tupdate\ttook-refused\tHEAD 已經包含擋過的 aaa\n"
        self.assertIn("bbb", self.probe(took)["unchecked"][3])
        self.assertIsNone(self.probe(took + back)["unchecked"], "update back: cleared")
        self.assertIsNotNone(self.probe(took + back + took)["unchecked"], "and told again if it happens again")
        u = self.probe(took)["unchecked"]
        now = u[4] + 300
        sick = healthy(now=now, last_ok={"sync": now, "update": u[4] - 600}, alerts=[], unchecked=u)
        code, recs = self.records({"host-a": sick})
        by = {r["id"]: r for r in recs}
        self.assertEqual(by["core.took-unchecked"]["status"], "fail")
        self.assertIn("aaa", by["core.took-unchecked"]["msg"])
        self.assertIn("bbb", by["core.took-unchecked"]["msg"])
        told = []
        from altention import update
        inv = {"machines": {"host-a": {"ssh": ["HOST-A"], "roles": ["chatlog"], "always_on": False}}}
        with mock.patch.object(update, "alert", lambda *a, **k: told.append(a)):
            self.run_doctor({"host-a": sick}, ["--watch"], inv=inv)
        self.assertEqual([a[1] for a in told], ["host-a/core.took-unchecked"], "told, even for a machine not always on")
        code, recs = self.records({"host-a": healthy(now=now, last_ok={"sync": now, "update": now}, alerts=[], unchecked=None)})
        self.assertEqual({r["id"]: r for r in recs}["core.took-unchecked"]["status"], "ok")
        # #code-review: update.py worked after that line though it wrote no took-refused (no record by then): gone
        code, recs = self.records({"host-a": healthy(now=now, last_ok={"sync": now, "update": u[4] + 60}, alerts=[], unchecked=u)})
        self.assertEqual({r["id"]: r for r in recs}["core.took-unchecked"]["status"], "ok")

    def test_a_fallback_used_is_told_until_that_one_works_again(self):
        fb = "2026-10-07T13:01:00+0800\tcron-update\tfallback\tupdate.py 沒跑完；改用單純的快轉：快轉好了\n"
        broken = "2026-10-07T13:02:00+0800\tsync\tupdate-broken\t載不進 update.py\n"
        sfb = "2026-10-07T13:03:00+0800\tcron-sync\tfallback\tsync 沒跑完；改用單純的快轉：沒做（鎖被拿著）\n"
        got = self.probe(fb + broken + sfb)["fallbacks"]
        self.assertEqual(got["update"][2], "update-broken", "the newest for update.py")
        self.assertEqual(got["sync"][1], "cron-sync")
        at = got["update"][4]
        # #code-review: against the last round that came to its end (last_alive-update), not last_ok: a round that
        # refuses writes alive only, and update.py is running again all the same
        for alive, status in ((at - 60, "fail"), (at + 60, "ok"), (None, "fail")):
            code, recs = self.records({"host-a": healthy(now=at + 120, last_ok={"sync": at + 300, "update": at - 999},
                                                         update_alive_at=alive, alerts=[], fallbacks=got)})
            by = {r["id"]: r for r in recs}
            self.assertEqual(by["core.fallback-update"]["status"], status, alive)
            self.assertEqual(by["core.fallback-sync"]["status"], "ok", "the sync worked after its fallback")
        self.assertIn("core.fallback-update", doctor.WATCHED)
        self.assertIn("core.fallback-sync", doctor.WATCHED)
        self.assertIn("core.took-unchecked", doctor.WATCHED)
        self.assertNotIn("core.alerts", doctor.WATCHED, "the rest of the alerts file stays a warn")

    def test_watch_from_another_checkout_tells_nothing(self):
        now = 2000000000
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        from altention import update
        told = []
        with mock.patch.object(update, "alert", lambda *a, **k: told.append(a)), \
                mock.patch.object(update, "verdict", return_value=("other", "a worktree")):
            code, text = self.run_doctor({"host-a": healthy(now=now, last_ok={"sync": now - 9999, "update": now}, alerts=[])}, ["--watch"])
        self.assertEqual((code, told), (0, []))
        self.assertIn("不是這台的正式引擎", text)

    def test_a_cron_line_that_says_it_did_not_run_shows(self):
        now = 2000000000
        code, recs = self.records({"host-a": healthy(now=now, last_ok={"sync": now, "update": now}, alerts=[],
                                                     cron_logs={"update": "2026 不是這台的正式引擎：x；什麼都沒做", "sync": "2026 這一輪跑完（結束碼 0）"})})
        ids = {r["id"]: r for r in recs if r["machine"] == "host-a"}
        self.assertEqual(ids["core.cron-update-log"]["status"], "warn")
        self.assertNotIn("core.cron-sync-log", ids)

    def test_a_machine_not_always_on_asleep_is_not_a_fault_nor_told(self):
        now = 2000000000
        inv = {"machines": {"lap": {"ssh": ["LAP"], "roles": ["chatlog"], "always_on": False},
                            "host-a": {"ssh": ["HOST-A"], "roles": ["chatlog"]}}}
        probes = {"lap": healthy(now=now, last_ok={"sync": now - 3 * 3600, "update": now - 3 * 3600}, alerts=[]),
                  "host-a": healthy(now=now, last_ok={"sync": now, "update": now}, alerts=[])}
        code, recs = self.records(probes, inv=inv)
        lap = {r["id"]: r for r in recs if r["machine"] == "lap"}
        self.assertEqual(lap["core.last-sync-ok"]["status"], "warn")
        self.assertIn("不常開", lap["core.last-sync-ok"]["msg"])
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        from altention import update
        told = []
        with mock.patch.object(update, "alert", lambda *a, **k: told.append(a)), \
                mock.patch.object(doctor, "inventory", return_value=json.loads(json.dumps(inv))):
            self.run_doctor({"lap": None, "host-a": probes["host-a"]}, ["--watch"], inv=inv)
        self.assertEqual(told, [], "asleep or unreachable, a machine not always on is not told about")

    def test_a_machine_that_does_not_know_its_own_name_is_a_problem(self):
        code, recs = self.records({"host-a": healthy(this_machine="host-a-lab")})
        self.assertEqual(code, 1)
        r = next(r for r in recs if r["id"] == "core.machine-name")
        self.assertEqual(r["status"], "fail")
        self.assertIn("host-a-lab", r["msg"])
        self.assertIn("~/.config/altention/host", r["fix"])
        code, recs = self.records({"host-a": healthy()})
        self.assertEqual(next(r for r in recs if r["id"] == "core.machine-name")["status"], "ok")

    def test_the_same_command_twice_in_the_crontab_is_a_problem(self):
        code, recs = self.records({"host-a": healthy(cron_dupes=["/usr/bin/python3 /e/planner/remind.py x"])})
        self.assertEqual(code, 1)
        dup = next(r for r in recs if r["id"] == "core.cron-duplicate")
        self.assertEqual(dup["status"], "fail")
        self.assertIn("plugins.py plan", dup["fix"])
        code, recs = self.records({"host-a": healthy(cron_dupes=[])})
        self.assertEqual(next(r for r in recs if r["id"] == "core.cron-duplicate")["status"], "ok")

    def test_fix_flag_and_exit_status(self):
        code, text = self.run_doctor({"host-a": healthy()})
        self.assertEqual((code, text.strip().splitlines()[-1]), (0, "全部正常。"))
        scan = doctor.record("core.privacy-scan", None, "warn", "個資掃描：引擎已提交的樹裡有 1 筆、1 個檔案", "看明細", 1)
        code, text = self.run_doctor({"host-a": healthy()}, scan=scan)
        self.assertEqual(code, 0, "a warning is shown, and is not a problem")
        self.assertIn("個資掃描", text)
        self.assertIn("全部正常。", text)
        code, text = self.run_doctor({"host-a": healthy(hooks=False)}, ["--fix"])
        self.assertEqual(code, 1)
        self.assertIn("- host-a：chatlog：沒有 hooks\n  修：python3 ops/apply.py host-a", text)
        with self.assertRaises(SystemExit) as stop:
            self.run_doctor({"host-a": healthy()}, ["--jsno"])
        self.assertIn("--jsno", str(stop.exception))
        with self.assertRaises(SystemExit) as stop:
            self.run_doctor({"host-a": healthy()}, ["host-z"])
        self.assertIn("host-z", str(stop.exception))

    def test_one_machine_without_a_list_checks_no_strays_versions_or_ssh(self):
        inv = {"machines": {"host-a": {"ssh": ["local"], "roles": []}}, "single": True}
        code, recs = self.records({"host-a": healthy(code="older99")}, inv=inv)  # crons of every role, another version
        self.assertEqual(code, 0, [r for r in recs if r["status"] != "ok"])
        ids = [r["id"] for r in recs]
        for absent in ("core.engine-version", "chatlog.not-listed", "dcbot.not-listed", "progress.not-listed", "progress.single"):
            self.assertNotIn(absent, ids)
        self.assertEqual(next(r for r in recs if r["id"] == "core.machine-list")["value"], "single")
        self.assertEqual(next(r for r in recs if r["id"] == "core.reachable")["value"], "local")
        code, text = self.run_doctor({"host-a": healthy()}, inv=inv)
        self.assertIn("（沒有機器清單：只有這台，不比對各機器版本）", text)

    def test_placements_not_known_say_so_and_nothing_else(self):
        """#architecture-review 2026-10-15: not known is neither on nor off: no plugin checks, no "should not run here"."""
        probes = {"host-a": healthy()}  # its crons of chatlog and progress are there
        inv = {"machines": {"host-a": {"ssh": ["A"], "roles": []}, "host-b": {"ssh": ["B"], "roles": []}}}
        for plugins_on in (lambda n: None, lambda n: (set(), {"chatlog", "progress"}), lambda n: ({"chatlog"}, {"progress"})):
            with mock.patch.object(doctor, "plugins_on", plugins_on):
                out = io.StringIO()
                with mock.patch.object(doctor, "inventory", return_value=json.loads(json.dumps(inv))), \
                        mock.patch.object(doctor, "run_on", lambda m, sc, **k: (m["ssh"][0], 0, json.dumps(
                            dict(probes["host-a"], this_machine={"A": "host-a", "B": "host-b"}[m["ssh"][0]])), "")), \
                        mock.patch.object(doctor, "local_head_status", return_value=("abc1234", "")), \
                        mock.patch.object(doctor, "privacy_record", return_value=None), contextlib.redirect_stdout(out):
                    doctor.main(["--json"])
            recs = json.loads(out.getvalue())
            ids = {(r["machine"], r["id"]): r for r in recs}
            self.assertFalse([k for k in ids if k[1] in ("chatlog.not-listed", "progress.not-listed", "progress.cron")], ids)
            self.assertEqual(ids["host-a", "core.placements"]["status"], "warn")
            self.assertIn("progress", ids["host-a", "core.placements"]["msg"])
            # chatlog placed here (#code-review 2026-10-15: its own segment list once took the name of what is not placed)
            self.assertEqual(("host-a", "chatlog.cron") in ids, "chatlog" in (plugins_on("host-a") or (set(), set()))[0])

    def test_a_chatlog_that_never_synced_yet_is_shown_not_a_problem(self):
        old = (doctor.datetime.now(doctor.timezone.utc) - doctor.timedelta(hours=3)).isoformat(timespec="seconds")
        code, recs = self.records({"host-a": healthy(chatlog_last_sync=None), "host-b": healthy(chatlog_last_sync=old)})
        got = {(r["machine"], r["id"]): r["status"] for r in recs}
        self.assertEqual(got["host-a", "chatlog.last-sync"], "warn", "installed a moment ago")
        self.assertEqual(got["host-b", "chatlog.last-sync"], "fail", "it did sync, three hours ago")

    def test_what_runs_where_comes_from_plugins_json_not_the_roles(self):
        inst = tempfile.mkdtemp(prefix="實例 ")
        self.addCleanup(shutil.rmtree, inst, True)
        with open(os.path.join(inst, "machines.json"), "w") as f:
            json.dump({"format": 1, "machines": {"host-a": {"ssh": ["local"], "roles": ["progress", "usage"]},
                                                 "host-b": {"ssh": ["B"], "roles": ["dcbot"]}}}, f)
        with open(os.path.join(inst, "plugins.json"), "w") as f:
            json.dump({"format": 1, "plugins": {"progress": {"on": "host-b"}, "chatlog": {"disabled_on": ["host-a"]}}}, f)
        with mock.patch.dict(os.environ, {"ALT_INSTANCE": inst, "ALT_ROOT": os.path.join(inst, "root")}):
            (a, _), (b, _) = doctor.plugins_on("host-a"), doctor.plugins_on("host-b")
        self.assertNotIn("progress", a, "the role says progress, plugins.json does not")
        self.assertNotIn("usage", a)
        self.assertIn("progress", b)
        self.assertNotIn("chatlog", a)
        self.assertIn("chatlog", b)

    def test_with_a_list_versions_and_strays_are_checked(self):
        inv = {"machines": {"host-a": {"ssh": ["A"], "roles": ["chatlog", "progress", "dcbot"]}, "host-b": {"ssh": ["B"], "roles": []}}}
        code, recs = self.records({"host-a": healthy(), "host-b": healthy(code="older99")}, inv=inv)
        fails = [(r["machine"], r["id"]) for r in recs if r["status"] == "fail"]
        self.assertEqual(fails, [("host-b", "core.engine-version"), ("host-b", "chatlog.not-listed"),
                                 ("host-b", "dcbot.not-listed"), ("host-b", "progress.not-listed"), (None, "progress.single")])
        self.assertIn("host-a, host-b", recs[-1]["msg"])

    def test_sessions_of_deleted_channels_the_bot_did_not_close(self):
        """Manager 2026-10-11 (#issues 10): what cannot be closed on plain facts is listed for the owner."""
        def item(**probe):
            _, recs = self.records({"host-a": healthy(**probe)})
            return next((r for r in recs if r["id"] == "dcbot.ownerless-sessions"), None)
        r = item(now=2000000000, ownerless={"at": 2000000000 - 7200, "kept": [{"name": "dc-project-a-0042", "channel": "100000000000000042", "idle_hours": 30.5,
                                               "why": "它底下還有行程在跑（背景工作？pid 77）"}]})
        self.assertEqual((r["status"], r["value"]), ("warn", 1))
        self.assertIn("dc-project-a-0042（原本的頻道 100000000000000042，閒置 30.5 小時，它底下還有行程在跑", r["msg"])
        self.assertIn("kill-session", r["fix"])
        self.assertIn("可能已經不是現況", r["msg"], "written long ago: the bot has not looked since")
        fresh = item(now=2000000000, ownerless={"at": 2000000000 - 600, "kept": [{"name": "dc-x-0001", "channel": "1", "idle_hours": 13, "why": "有一輪在跑"}]})
        self.assertNotIn("可能已經不是現況", fresh["msg"])
        self.assertEqual(item(ownerless={"at": "x", "kept": [{"name": "dc-x-0001"}]})["status"], "warn", "a time that is none: still shown")
        self.assertEqual(item(ownerless={"at": 1, "kept": []})["status"], "ok")
        self.assertEqual(item(ownerless={})["status"], "ok", "never written")
        self.assertEqual(item(ownerless={"kept": "x"})["status"], "ok")
        self.assertEqual(item(ownerless={"kept": [5, {"name": 7}]})["status"], "ok", "rows that are no rows")
        self.assertIsNone(item(), "an engine that does not probe it")
        self.assertNotIn("dcbot.ownerless-sessions", doctor.WATCHED + doctor.WATCHED_WARN)

    def test_notifications_the_bot_could_show_nowhere(self):
        """Manager 2026-10-11 (#issues 7): a notification kept in dcbot/state/posts-failed/ is a warning."""
        def item(**probe):
            _, recs = self.records({"host-a": healthy(**probe)})
            return next((r for r in recs if r["id"] == "dcbot.posts-failed"), None)
        r = item(posts_failed=3)
        self.assertEqual((r["status"], r["value"]), ("warn", 3))
        self.assertIn("3 則通知", r["msg"])
        self.assertIn("posts-failed", r["fix"])
        self.assertEqual(item(posts_failed=0)["status"], "ok")
        self.assertEqual(item(posts_failed="many")["status"], "ok", "not a number: as if not probed, said by core.probe-odd")
        self.assertIsNone(item(), "an engine that does not probe it")
        self.assertNotIn("dcbot.posts-failed", doctor.WATCHED + doctor.WATCHED_WARN)

    def test_routes_without_their_panes_process_are_listed(self):
        """Stage 3b step 1.9: the first format of a route (just the channel) and one with no process are still read;
        shown for the owner to attach again, nothing is failing."""
        def item(**probe):
            _, recs = self.records({"host-a": healthy(**probe)})
            return next((r for r in recs if r["id"] == "dcbot.routes-without-pid"), None)
        r = item(routes_without_pid=["100000000000000042", "7"])
        self.assertEqual(r["status"], "warn")
        self.assertIn("有 2 筆舊格式的路由", r["msg"])
        self.assertIn("100000000000000042、7", r["msg"])
        self.assertIn("/detach 再 /attach", r["fix"])
        self.assertEqual((item(routes_without_pid=[])["status"], item(routes_without_pid=[])["msg"]), ("ok", ""))
        self.assertEqual(item(routes_without_pid="x")["status"], "ok", "not a list: as none")
        self.assertIsNone(item(), "a machine whose engine is a step behind does not say: no item")
        self.assertNotIn("dcbot.routes-without-pid", doctor.WATCHED + doctor.WATCHED_WARN)

    def test_the_bot_checks_stop_at_the_first_that_fails(self):
        inv = {"machines": {"host-a": {"ssh": ["A"], "roles": ["dcbot"]}}}
        code, recs = self.records({"host-a": healthy(bot_cron=False, bot_supervisor=False, cron_chatlog=False, hooks=False,
                                                     cron_progress=False)}, inv=inv)
        bot = [(r["id"], r["status"]) for r in recs if r["id"].startswith("dcbot.") and r["id"] != "dcbot.events-bad"
               and r["id"] != "dcbot.stray-settings" and r["id"] != "dcbot.routes-without-pid"]
        self.assertEqual(bot, [("dcbot.settings", "ok"), ("dcbot.cron", "fail")])

    def test_each_rescued_edit_is_a_problem_of_its_own(self):
        sync = {"engine": {"rescued": ["stash@{0}: 改到一半 的檔", "altention-saved/2"]}, "instance": {}, "links": {"memory": "instance"}}
        code, recs = self.records({"host-a": healthy(sync=sync)})
        rescued = [r for r in recs if r["id"] == "sync.rescued"]
        self.assertEqual(len(rescued), 2)
        self.assertIn("改到一半 的檔", rescued[0]["msg"])
        code, text = self.run_doctor({"host-a": healthy(sync=sync)})
        self.assertIn("有 2 個問題", text)

    def test_values_behind_the_table(self):
        stamp = "2026-01-02T03:04:05+00:00"
        inv = {"machines": {"host-a": {"ssh": ["A"], "roles": ["chatlog", "progress", "dcbot"], "always_on": False}}}
        code, recs = self.records({"host-a": healthy(chatlog_last_sync=stamp, progress_running=True, tmp_space=91)}, inv=inv)
        value = {r["id"]: r.get("value") for r in recs}
        self.assertEqual((value["chatlog.last-sync"], value["progress.last-run"], value["core.tmp-space"], value["core.engine"]),
                         (stamp, "running", 91, "abc1234"))
        late = next(r for r in recs if r["id"] == "chatlog.last-sync")
        self.assertEqual(late["status"], "ok", "a machine that sleeps is not late")
        self.assertEqual([r["id"] for r in recs if r["status"] == "fail"], ["core.tmp-space"])


def backup_row(name, **kw):
    """One row as ops/remote.py status() writes it (the probe adds mode): a repo with an origin, our hook, all pushed."""
    row = {"name": name, "folder": "/home/alice/data/%s" % name, "repo": True, "origin": "git@example.com:alice/%s.git" % name,
           "hook": True, "ahead": 0, "last_push": time.time() - 600, "mode": 0o700,
           "check": {"verdict": "private", "why": "要登入", "at": time.time()}}
    if name != "instance":
        row["setting"] = row["origin"]
    row.update(kw)
    return row


class Backups(unittest.TestCase):
    """C-25 (data-safety 2.5, 3): each backup said on its own line: none, set but not attached, public, could not be
    told, the last push; and the private folders that are not 700."""
    ROLES = ["chatlog", "progress", "planner", "usage", "dcbot"]

    def got(self, rows, roles=None, **probe):
        t = Records("test_an_engine_from_an_archive_is_no_missing_engine")  # its helpers, not its test
        inv = {"machines": {"host-a": {"ssh": ["HOST-A"], "roles": roles if roles is not None else self.ROLES}}}
        probe.setdefault("usage_mode", 0o700)
        _, recs = t.records({"host-a": healthy(backup=rows, **probe)}, inv=inv)
        return {r["id"]: r for r in recs if r["id"].startswith("backup.") or r["id"] == "instance.remote"}

    def test_each_one_said(self):
        rows = [backup_row("instance", origin="", check=None),
                backup_row("logs", origin="", check=None),
                backup_row("progress", check={"verdict": "public", "why": "讀得到", "at": 1}),
                backup_row("planner", check={"verdict": "unknown", "why": "連不上", "at": 1})]
        got = self.got(rows)
        self.assertEqual(got["backup.instance"]["status"], "warn")
        self.assertIn("沒有備份：只在這台，電腦壞了就救不回來", got["backup.instance"]["msg"])
        self.assertIn("python3 ops/remote.py set instance <private repo 網址>", got["backup.instance"]["fix"])
        self.assertIn("備份與還原", got["backup.instance"]["fix"])
        self.assertIn("設了遠端 git@example.com:alice/logs.git", got["backup.logs"]["msg"])
        self.assertIn("沒有接上", got["backup.logs"]["msg"])
        self.assertIn("python3 ops/remote.py set logs git@example.com:alice/logs.git", got["backup.logs"]["fix"])
        self.assertEqual(got["backup.progress"]["status"], "fail")
        self.assertIn("公開的 repo", got["backup.progress"]["msg"])
        self.assertEqual(got["backup.planner"]["status"], "warn")
        self.assertIn("沒辦法確認是不是公開的", got["backup.planner"]["msg"])
        self.assertIn("照樣推送", got["backup.planner"]["msg"])
        self.assertNotIn("instance.remote", got, "said once, as a backup")

    def test_all_backed_up_is_ok_with_the_last_push(self):
        got = self.got([backup_row(n) for n in ("instance", "logs", "progress", "planner")])
        for n in ("instance", "logs", "progress", "planner"):
            self.assertEqual(got["backup.%s" % n]["status"], "ok", got["backup.%s" % n])
            self.assertIn("上次推送：", got["backup.%s" % n]["msg"])
        self.assertEqual(got["backup.private"]["status"], "ok")

    def test_the_branch_a_backup_came_from_is_said(self):
        """C-43: the records or progress cloned from a remote whose only branch was not main push to a new main."""
        got = self.got([backup_row("instance"), backup_row("logs", branch="main", remote_branches=["master", "main"]),
                        backup_row("planner", branch="mine", remote_branches=["mine"])])
        self.assertEqual(got["backup.logs"]["status"], "ok")
        self.assertIn("遠端原本的分支是 master，之後推到 main", got["backup.logs"]["msg"])
        self.assertNotIn("遠端原本的分支", got["backup.planner"]["msg"])
        self.assertNotIn("遠端原本的分支", got["backup.instance"]["msg"])

    def test_set_but_not_a_repo_yet_and_an_origin_not_the_setting(self):
        rows = [backup_row("instance"), backup_row("progress", repo=False, origin=None, hook=None),
                backup_row("logs", origin="git@example.com:alice/other.git"),
                backup_row("planner", hook=False)]
        got = self.got(rows)
        self.assertIn("還不是 git repo：還沒接上", got["backup.progress"]["msg"])
        self.assertIn("./install.sh", got["backup.progress"]["fix"])
        self.assertIn("跟設定的 git@example.com:alice/logs.git 不一樣", got["backup.logs"]["msg"])
        self.assertIn("--replace", got["backup.logs"]["fix"])
        self.assertIn("pre-push hook", got["backup.planner"]["msg"])
        self.assertIn("python3 ops/remote.py hook planner", got["backup.planner"]["fix"])

    def test_commits_not_pushed_for_a_day_and_the_sync_failing(self):
        rows = [backup_row("instance", ahead=3, last_push=time.time() - 3 * 86400, waiting_since=time.time() - 2 * 86400),
                backup_row("planner")]
        sync = {"engine": {}, "instance": {}, "links": {"memory": "instance"},
                "data": {"planner/reminders": {"at": 1, "ok": False, "error": "rebase 失敗（兩台改到同一段）", "path": "/x"}}}
        got = self.got(rows, sync=sync)
        self.assertIn("3 個 commit 一天以上沒推上去", got["backup.instance"]["msg"])
        self.assertIn("rebase 失敗", got["backup.planner"]["msg"])
        got = self.got([backup_row("instance", ahead=3, last_push=time.time() - 3 * 86400, waiting_since=time.time() - 600)])
        self.assertEqual(got["backup.instance"]["status"], "ok", "made ten minutes ago: the next round pushes them")
        got = self.got([backup_row("instance", ahead=None, last_push=None, unpushed=2, waiting_since=time.time() - 2 * 86400)])
        self.assertIn("2 個 commit 一天以上沒推上去", got["backup.instance"]["msg"], "#code-review of bc09467: no @{u}")
        self.assertIn("還沒有上游分支", got["backup.instance"]["msg"])
        got = self.got([backup_row("planner", ahead=1, last_push=None, waiting_since=time.time() - 60)])
        self.assertEqual(got["backup.planner"]["status"], "ok", "just cloned back (the clean-container run): no push yet")

    def test_a_part_not_on_there_or_never_made_is_not_said(self):
        rows = [backup_row("instance"), backup_row("logs"), backup_row("progress"),
                backup_row("planner", repo=False, origin=None, setting="")]
        got = self.got(rows, roles=["chatlog"])
        self.assertEqual(sorted(got), ["backup.instance", "backup.logs", "backup.private"])
        got = self.got(rows)
        self.assertNotIn("backup.planner", got, "no folder and nothing set: nothing to lose yet")

    def test_folders_not_700_are_named_with_chmod(self):
        rows = [backup_row("instance", mode=0o755, folder="/home/alice/我的 實例"), backup_row("logs")]
        got = self.got(rows, usage_mode=None)
        self.assertEqual(got["backup.private"]["status"], "warn")
        self.assertIn("/home/alice/我的 實例", got["backup.private"]["msg"])
        self.assertIn("chmod 700 '/home/alice/我的 實例'", got["backup.private"]["fix"])
        t = Records("test_an_engine_from_an_archive_is_no_missing_engine")
        inv = {"machines": {"host-a": {"ssh": ["HOST-A"], "roles": self.ROLES}}}
        _, recs = t.records({"host-a": healthy(backup=[backup_row("instance")], usage_mode=0o755)}, inv=inv)
        private = [r for r in recs if r["id"] == "backup.private"][0]
        self.assertIn("~/.local/share/usage", private["fix"])

    def test_an_older_engine_keeps_the_old_line_and_a_status_not_read_is_unknown(self):
        t = Records("test_an_engine_from_an_archive_is_no_missing_engine")
        inv = {"machines": {"host-a": {"ssh": ["HOST-A"], "roles": self.ROLES}}}
        _, recs = t.records({"host-a": healthy(instance_remote="")}, inv=inv)
        self.assertIn("instance.remote", [r["id"] for r in recs])
        got = self.got([], backup_error="ImportError: no module named remote")
        self.assertEqual(got["backup.status"]["status"], "unknown")
        self.assertIn("python3 ops/remote.py status", got["backup.status"]["fix"])

    def test_the_owners_own_secretary_folder_is_never_asked_to_get_our_hook(self):
        """planner.folder (our ~/life): our hook would have the sync commit and push all of it (batch 4 ruling);
        found on our own machines in the read-only run, 2026-10-17."""
        got = self.got([backup_row("instance"), backup_row("planner", hook=False, setting="", own=True, folder="/home/alice/life")])
        self.assertEqual(got["backup.planner"]["status"], "ok")
        self.assertIn("planner.folder", got["backup.planner"]["msg"])
        self.assertNotIn("hook", got["backup.planner"]["fix"])
        got = self.got([backup_row("planner", origin="", hook=False, setting="", own=True)])
        self.assertEqual(got["backup.planner"]["status"], "warn")
        self.assertNotIn("remote.py", got["backup.planner"]["fix"])
        got = self.got([backup_row("planner", own=True, check={"verdict": "public", "why": "讀得到", "at": 1})])
        self.assertEqual(got["backup.planner"]["status"], "fail")

    def test_odd_fields_cost_the_field_not_the_check(self):
        rows = [backup_row("instance", ahead="many", mode="rwx", check="private"), "not a row"]
        t = Records("test_an_engine_from_an_archive_is_no_missing_engine")
        inv = {"machines": {"host-a": {"ssh": ["HOST-A"], "roles": self.ROLES}}}
        _, recs = t.records({"host-a": healthy(backup=rows)}, inv=inv)
        odd = [r for r in recs if r["id"] == "core.probe-odd"]
        self.assertTrue(odd)
        _, recs = t.records({"host-a": healthy(backup=[backup_row("instance", ahead="many", mode="rwx", check="private")])}, inv=inv)
        self.assertEqual([r["status"] for r in recs if r["id"] == "backup.instance"], ["ok"])

    def test_the_probe_reads_each_backup_there(self):
        """The real probe, in a throwaway home: ops/remote.py status's rows, with each folder's mode."""
        home = tempfile.mkdtemp(prefix="家 備份 ")
        try:
            inst = os.path.join(home, "altention", "instance")
            os.makedirs(inst)
            os.chmod(inst, 0o755)
            with open(os.path.join(inst, "plugins.json"), "w") as f:
                json.dump({"format": 1, "sync": {"dirs": {"chatlog": {"remote": "git@example.com:alice/logs.git"}}},
                       "planner": {"folder": os.path.join(home, "life")}}, f)
            subprocess.run(["git", "-C", inst, "init", "-q"], check=True)
            bare = os.path.join(home, "inst.git")
            git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", inst]
            subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
            subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "a"], check=True)
            subprocess.run(git + ["remote", "add", "origin", bare], check=True)
            subprocess.run(git + ["push", "-q", "-u", "origin", "HEAD:main"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(git + ["branch", "-q", "--set-upstream-to=origin/main"], check=True)
            subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "b"], check=True)
            env = dict(os.environ, HOME=home, ALT_INSTANCE=inst, ALT_DATA=os.path.join(home, "data"))
            r = subprocess.run([sys.executable, "-"], input="ENGINE = %r\n" % doctor.ENGINE_DIR + doctor.PROBE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, universal_newlines=True, env=env, timeout=120)
            o = json.loads(r.stdout.strip().splitlines()[-1])
            self.assertNotIn("backup_error", o, o.get("backup_error"))
            rows = {row["name"]: row for row in o["backup"]}
            self.assertEqual(sorted(rows), ["instance", "logs", "planner", "progress"])
            self.assertEqual((rows["instance"]["repo"], rows["instance"]["origin"], rows["instance"]["mode"]), (True, bare, 0o755))
            self.assertEqual(rows["instance"]["ahead"], 1)
            self.assertLess(abs(rows["instance"]["waiting_since"] - time.time()), 120, "the commit not pushed, just made")
            self.assertEqual(rows["instance"]["unpushed"], 1)
            subprocess.run(git + ["branch", "-q", "--unset-upstream"], check=True)
            o = json.loads(subprocess.run([sys.executable, "-"], input="ENGINE = %r\n" % doctor.ENGINE_DIR + doctor.PROBE,
                                          stdout=subprocess.PIPE, universal_newlines=True, env=env, timeout=120).stdout.strip().splitlines()[-1])
            row = [r for r in o["backup"] if r["name"] == "instance"][0]
            self.assertEqual((row["ahead"], row["unpushed"]), (None, 1), "no upstream: still counted")
            self.assertIsNotNone(row["waiting_since"])
            self.assertEqual((rows["logs"]["repo"], rows["logs"]["setting"]), (False, "git@example.com:alice/logs.git"))
            self.assertIs(rows["planner"].get("own"), True, "planner.folder: the owner's own")
            self.assertNotIn("own", rows["logs"])
        finally:
            shutil.rmtree(home, ignore_errors=True)


class Schedules(unittest.TestCase):
    """C-37 B (manager 2026-10-16/17): how long without a run is a problem comes from the schedule as it runs (the
    instance's override included): two turns in a row missed; a new line's first turn the same way; an item the
    instance turned off is no problem; an override that cannot be used is said."""
    ZONE = {"core": {"timezone": "Asia/Taipei"}}
    unset, off, needs = {}, {}, {}
    run_doctor, records = Records.run_doctor, Records.records

    def settings(self, **schedules):
        s = json.loads(json.dumps(self.ZONE))
        s["plugins"] = {n: {"schedules": o} for n, o in schedules.items()}
        return mock.patch("altention.config.settings", return_value=s)

    @staticmethod
    def at(text):
        return doctor.datetime.strptime(text + " +0800", "%Y-%m-%d %H:%M %z").timestamp()

    def test_two_turns_missed_daily_and_every_ten_minutes(self):
        with self.settings(progress={"run": {"when": {"at": "03:00", "days": "daily"}}}):
            sched = doctor.schedule_of("progress", "run", "host-a")
        self.assertEqual(sched["within"], 9 * 60, "progress's own within")
        now = self.at("2026-10-20 10:00")
        self.assertFalse(doctor.missed_two(sched, self.at("2026-10-19 03:20"), now), "yesterday's ran: today's missed is one")
        self.assertTrue(doctor.missed_two(sched, self.at("2026-10-18 03:20"), now), "yesterday's and today's missed")
        self.assertTrue(doctor.missed_two(sched, None, now))
        self.assertFalse(doctor.missed_two(sched, self.at("2026-10-18 03:20"), self.at("2026-10-20 03:05")),
                         "today's has its 9 minutes to finish")
        with self.settings():
            sched = doctor.schedule_of("progress", "run", "host-a")
        now = self.at("2026-10-20 10:00")
        self.assertFalse(doctor.missed_two(sched, now - 15 * 60, now))
        self.assertTrue(doctor.missed_two(sched, now - 35 * 60, now))

    def test_a_new_lines_first_turn_follows_its_time(self):
        with self.settings(progress={"run": {"when": {"at": "03:00", "days": "daily"}}}):
            sched = doctor.schedule_of("progress", "run", "host-a")
        self.assertEqual(doctor.first_wait(sched, self.at("2026-10-20 10:00")), 17 * 60 + 9 + 5, "tomorrow 03:00, its within, slack")
        with self.settings():
            sched = doctor.schedule_of("progress", "run", "host-a")
        self.assertEqual(doctor.first_wait(sched, self.at("2026-10-20 10:02")), 8 + 9 + 5)

    def test_progress_once_a_day_is_not_said_late_after_twenty_hours(self):
        """The owner's report: progress set to once a day, and the dashboard said 「進度上次執行」 all day."""
        def last_run(hours):
            return (doctor.datetime.now(doctor.timezone.utc) - doctor.timedelta(hours=hours)).isoformat(timespec="seconds")
        with self.settings(progress={"run": {"when": {"at": "03:00", "days": "daily"}}}):
            _, recs = self.records({"host-a": healthy(progress_last_run=last_run(20))})
            self.assertNotIn("progress.last-run", [r["id"] for r in recs if r["status"] != "ok"])
            _, recs = self.records({"host-a": healthy(progress_last_run=last_run(50))})
            self.assertIn("進度上次執行", [r for r in recs if r["id"] == "progress.last-run"][0]["msg"])
        with self.settings():
            _, recs = self.records({"host-a": healthy(progress_last_run=last_run(20))})
            self.assertIn("進度上次執行", [r for r in recs if r["id"] == "progress.last-run"][0]["msg"], "every 10 minutes: late")

    def test_an_item_turned_off_is_no_problem_and_said(self):
        with self.settings(progress={"run": {"enabled": False}}):
            code, recs = self.records({"host-a": healthy(cron_progress=False, progress_last_run=None)})
        by = {r["id"]: r for r in recs if r["machine"] == "host-a"}
        self.assertEqual((by["progress.cron"]["status"], by["progress.cron"]["value"]), ("ok", "關掉了（實例設定 plugins.progress.schedules.run）"))
        self.assertNotIn("progress.last-run", by)
        self.assertEqual((by["progress.schedules"]["status"], by["progress.schedules"]["value"]), ("ok", "run 關掉了（實例設定）"))

    def test_an_override_in_effect_is_shown(self):
        """(One that cannot be used is plugins.would-apply's to say, from the machine's own report.)"""
        with self.settings(progress={"run": {"when": {"every": "1h"}}}):
            _, recs = self.records({"host-a": healthy()})
        r = [r for r in recs if r["id"] == "progress.schedules"][0]
        self.assertEqual((r["status"], r["value"]), ("ok", 'run {"every": "1h"}（實例設定）'))
        with self.settings(progress={"run": {"when": {"every": "7m"}}, "rn": {"enabled": False}}):
            _, recs = self.records({"host-a": healthy()})
        self.assertFalse([r for r in recs if r["id"] == "progress.schedules"], "not shown as in effect, nor said twice")
        with self.settings():
            _, recs = self.records({"host-a": healthy()})
        self.assertFalse([r for r in recs if r["id"].endswith(".schedules")], "no override: nothing said")

    def test_the_sync_and_the_update_are_late_after_three_of_their_rounds(self):
        """C-37 C: as update.py and the sync count it, at the interval the instance set (sync.interval); 45 minutes
        when it cannot be told."""
        now = time.time()

        def late(intervals, minutes):
            _, recs = self.records({"host-a": healthy(now=now, last_ok={"sync": now - minutes * 60, "update": now}, intervals=intervals)})
            return [r for r in recs if r["id"] == "core.last-sync-ok" and r["status"] != "ok"]
        iv = {"sync": 3600, "engine-update": 900}
        self.assertEqual((doctor.core_stale("sync", iv), doctor.core_stale("engine-update", iv), doctor.core_stale("watch", iv)),
                         (3 * 3600, 45 * 60, 45 * 60))
        self.assertEqual(late(iv, 50), [], "once an hour on that machine: 50 minutes is not late")
        self.assertIn("已經 50 分鐘沒有成功", late(None, 50)[0]["msg"], "no intervals.json there: 45 minutes")
        for bad in ({"sync": "1h"}, {"sync": True}, {"sync": 5}, [1]):
            self.assertEqual(doctor.core_stale("sync", bad), 45 * 60, bad)

    def test_the_board_calls_records_old_after_two_watch_rounds(self):
        sys.path.insert(0, os.path.join(doctor.ENGINE_DIR, "status"))
        import boards
        with self.settings():
            self.assertEqual(boards.watch_stale(), 40 * 60)
        with mock.patch("altention.config.settings", return_value={"core": {"schedules": {"watch": {"when": {"every": "30m"}}}}}):
            self.assertEqual(boards.watch_stale(), 70 * 60)
        with mock.patch("altention.config.settings", side_effect=ValueError("broken")):
            self.assertEqual(boards.watch_stale(), boards.STALE)


class SilentSsh(unittest.TestCase):
    def test_ssh_failing_with_nothing_on_stderr_is_said_as_unreachable(self):
        """C-45 drill (2026-10-17): ssh exited 255 and printed nothing; `(err or rc)[:80]` took the int and the
        machine came out as 檢查出錯 (TypeError) instead of 連不上."""
        with mock.patch.object(doctor, "run_on", return_value=("OLDUP", 255, "", "")), \
                mock.patch.object(doctor, "plugins_on", return_value=(set(), set())):
            recs = doctor.check("old-laptop", {"ssh": ["OLDUP"], "roles": []}, "abc1234")
        reach = [r for r in recs if r["id"] == "core.reachable"]
        self.assertEqual([(r["status"], r["msg"]) for r in reach], [("fail", "連不上（255）")])


if __name__ == "__main__":
    unittest.main()
