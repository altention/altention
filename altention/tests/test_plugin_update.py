"""altention.plugin_update, C-61 E3's part (support-3's card calls these): a declared plugin's releases, what moving
to one would change here, and moving it, against a plugin repo made in a throwaway home."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from altention import plugin_update as pu


def git(*args, cwd=None):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.org", "-c", "init.defaultBranch=main"]
                   + list(args), cwd=cwd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False))


BASE = {"format": 1, "name": "demo", "title": "L", "description": "d", "machines": "all",
        "setup": [{"name": "tools", "run": "echo tools >> \"$HOME/ran.txt\""}]}


class Rig(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="家 e3 ")
        self.root = os.path.join(self.home, "altention")
        self.inst = os.path.join(self.home, "實例")
        os.makedirs(os.path.join(self.home, ".config", "altention"))
        write(os.path.join(self.home, ".config", "altention", "host"), "box\n")
        write(os.path.join(self.inst, "machines.json"), {"format": 1, "machines": {
            "box": {"ssh": ["local"], "roles": ["dcbot"]}, "far": {"ssh": ["far"], "roles": []}}})
        self.env = mock.patch.dict(os.environ, {"HOME": self.home, "ALT_INSTANCE": self.inst, "ALT_ROOT": self.root})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(shutil.rmtree, self.home, True)
        self.work = os.path.join(self.home, "work")
        self.src = os.path.join(self.home, "來源 demo.git")
        os.makedirs(self.work)
        git("init", "-q", self.work)
        self.release("v0.1.0", BASE, "- **第一版。** 說明。")
        git("clone", "-q", "--bare", self.work, self.src)
        self.declare({"source": self.src, "version": "v0.1.0"})
        git("init", "-q", self.inst)
        git("add", "-A", cwd=self.inst)
        git("commit", "-qm", "start", cwd=self.inst)

    def release(self, tag, d, zh_point, files=()):
        write(os.path.join(self.work, "plugin.json"), d)
        write(os.path.join(self.work, "CHANGELOG.md"),
              "# Changelog\n\n## %s — 2026-10-20\n\n### For people\n\n- **Point %s.** More.\n\n### For people (zh-TW)\n\n%s\n"
              % (tag, tag, zh_point))
        for name, text in files:
            write(os.path.join(self.work, name), text)
        git("add", "-A", cwd=self.work)
        git("commit", "-qm", tag, cwd=self.work)
        git("tag", tag, cwd=self.work)
        if os.path.isdir(getattr(self, "src", "")):
            git("push", "-q", self.src, "HEAD:refs/heads/main", tag, cwd=self.work)

    def declare(self, entry, core=None):
        d = {"format": 1, "plugins": {"demo": entry}}
        if core:
            d["core"] = core
        write(os.path.join(self.inst, "plugins.json"), d)

    def p(self, version="v0.1.0"):
        return {"name": "demo", "source": self.src, "version": version}

    def folder(self):
        return pu.folder_of("demo")

    def at(self):
        return subprocess.run(["git", "-C", self.folder(), "describe", "--tags"], stdout=subprocess.PIPE,
                              universal_newlines=True).stdout.strip()

    def clone(self):
        self.assertEqual(pu.ensure("demo", self.p())[0], "made")


class Enabled(Rig):
    def test_fields_and_the_update_mode(self):
        got = pu.enabled()
        self.assertEqual(got, [{"name": "demo", "source": self.src, "version": "v0.1.0", "update": "notify",
                                "machines": ["box", "far"]}])
        self.declare({"source": self.src, "version": "v0.1.0", "disabled_on": ["far"]}, core={"update": "release"})
        self.assertEqual((pu.enabled()[0]["update"], pu.enabled()[0]["machines"]), ("release", ["box"]))
        self.declare({"source": self.src, "version": "v0.1.0", "update": "notify", "on": "far"}, core={"update": "release"})
        self.assertEqual((pu.enabled()[0]["update"], pu.enabled()[0]["machines"]), ("notify", ["far"]))
        self.declare({"local": True})
        self.assertEqual(pu.enabled(), [], "a local plugin does not update")


class Newer(Rig):
    def test_from_the_mirror_without_a_folder_then_from_the_folder(self):
        self.release("v0.2.0", BASE, "- **第二版。**")
        self.release("v0.10.0", BASE, "- **第十版。**")
        self.assertEqual(pu.newer(self.p()), ["v0.10.0", "v0.2.0"], "by number, not by text")
        self.assertTrue(os.path.isdir(pu.mirror_of("demo")), "no folder here: a bare copy in the state folder")
        self.assertFalse(os.path.exists(self.folder()))
        self.clone()
        self.assertEqual(pu.newer(self.p("v0.2.0")), ["v0.10.0"])

    def test_a_source_not_reachable_is_none(self):
        self.assertIsNone(pu.newer(dict(self.p(), source=os.path.join(self.home, "沒有.git"))))


class Notes(Rig):
    def test_each_releases_points_in_the_installations_language(self):
        self.release("v0.2.0", BASE, "- **第二版的重點。** 後面。\n- **另一點。**")
        pu.newer(self.p())
        got = pu.notes(self.p(), ["v0.2.0"], lang="zh-TW")
        self.assertEqual(got, [("v0.2.0", ["**第二版的重點。**", "**另一點。**"], True)])


class Check(Rig):
    def test_kinds(self):
        hooked = dict(BASE, hooks=[{"event": "Stop", "command": "{python} x.py"}],
                      schedules=[{"name": "practice", "when": {"at": "13:00"}, "do": {"message": "練習"}}])
        self.release("v0.2.0", hooked, "- **hook。**")
        self.release("v0.3.0", dict(BASE, engine=">=9.0.0"), "- **新引擎。**")
        self.release("v0.4.0", dict(BASE, test="exit 3"), "- **測試不過。**")
        self.release("v0.5.0", dict(BASE, test="test -f plugin.json"), "- **測試過。**")
        self.release("v0.5.1", dict(BASE, uses={"dc.post": 99}), "- **新介面。**")
        write(os.path.join(self.work, "plugin.json"), "{broken")
        git("commit", "-qam", "broken", cwd=self.work)
        git("tag", "v0.6.0", cwd=self.work)
        git("push", "-q", self.src, "HEAD:refs/heads/main", "v0.6.0", cwd=self.work)
        self.clone()
        pu.newer(self.p())
        c = pu.check(self.p(), "v0.2.0", "v0.3.2")
        self.assertEqual(c["kind"], "perms")
        self.assertEqual(c["added"], ["新的 hook：Stop（它會看到你所有 Claude 對話的內容）", "新的排程 practice：每天 13:00"])
        self.assertEqual(pu.check(self.p(), "v0.3.0", "v0.3.2")["kind"], "engine")
        self.assertEqual(pu.check(self.p(), "v0.3.0", "v9.0.0")["kind"], "clean", "the engine this round ends with")
        t = pu.check(self.p(), "v0.4.0", "v0.3.2")
        self.assertEqual((t["kind"], "結束碼 3" in t["why"]), ("test", True))
        self.assertEqual(pu.check(self.p(), "v0.5.0", "v0.3.2")["kind"], "clean")
        u = pu.check(self.p(), "v0.5.1", "v0.3.2")  # C-94: its uses this engine does not offer
        self.assertEqual(u["kind"], "interface")
        self.assertEqual(u["why"], "demo 的 v0.5.1 在這版引擎上不能用：它需要 dc.post v99，這版引擎提供的是 v1")
        self.assertEqual(pu.check(self.p(), "v0.6.0", "v0.3.2")["kind"], "broken")
        self.assertEqual(pu.check(self.p(), "v9.9.9", "v0.3.2")["kind"], "unreachable")
        with open(os.path.join(self.folder(), "plugin.json"), "a") as f:
            f.write(" ")
        c = pu.check(self.p(), "v0.2.0", "v0.3.2")
        self.assertEqual((c["kind"], c["kinds"]), ("edits", ["edits", "perms"]))

    def test_a_rewrite_and_a_skill_are_more_that_it_does(self):
        """manager 2026-10-21 (#code-review-2 of 1323639): a rewrite of replies and a skill are lines of 「多了」, and a
        rewrite (as a hook) is one of the watchers asked about on their own; only what the new version adds."""
        rw = {"command": "{python} {plugin}/rw.py", "when": "."}
        sk = [{"name": "rigskill", "path": "skill"}]
        self.release("v0.2.0", dict(BASE, rewrite=rw), "- **改寫。**")
        self.release("v0.3.0", dict(BASE, rewrite=rw, skills=sk), "- **skill。**")
        self.release("v0.4.0", dict(BASE, rewrite=rw, skills=sk), "- **一樣。**")
        self.clone()
        pu.newer(self.p())
        c = pu.check(self.p(), "v0.2.0", "v0.3.2")
        self.assertEqual(c["kind"], "perms")
        self.assertEqual(c["added"], ["新的改寫回覆：{python} {plugin}/rw.py（每一則 Claude 的回覆貼出前都先交給它，它會看到你的對話內容）"])
        self.assertEqual(pu.check(self.p(), "v0.3.0", "v0.3.2")["added"][-1], "所有 Claude 都會讀到它的 skill：rigskill")
        self.assertEqual(pu.added(dict(BASE, rewrite=rw, skills=sk), dict(BASE, rewrite=rw, skills=sk)), [], "nothing new")
        self.assertEqual(pu.added(dict(BASE, rewrite=rw), dict(BASE, rewrite=dict(rw, command="other")))[0][:7], "改了的改寫回覆")

    def test_watchers(self):
        rw = {"command": "x", "when": "."}
        hook = {"event": "Stop", "command": "{plugin}/h.sh"}
        self.assertEqual(pu.watchers({}, dict(BASE, rewrite=rw)), ["改寫回覆"])
        self.assertEqual(pu.watchers({}, dict(BASE, hooks=[hook])), ["hook Stop"])
        self.assertEqual(pu.watchers({}, dict(BASE, hooks=[hook], rewrite=rw)), ["hook Stop", "改寫回覆"])
        self.assertEqual(pu.watchers({}, dict(BASE, skills=[{"name": "s", "path": "p"}])), [], "a skill is not asked again")
        self.assertEqual(pu.watchers(dict(BASE, hooks=[hook], rewrite=rw), dict(BASE, hooks=[hook], rewrite=rw)), [],
                         "only what the new version adds")

    def test_a_folder_not_ours_is_origin(self):
        self.release("v0.2.0", BASE, "- **x。**")
        self.clone()
        subprocess.run(["git", "-C", self.folder(), "remote", "set-url", "origin", "/elsewhere.git"], check=True)
        pu.newer(self.p())  # the mirror then
        self.assertEqual(pu.check(self.p(), "v0.2.0", "v0.3.2")["kind"], "origin")

    def test_a_file_of_theirs_where_the_new_version_adds_one_is_edits(self):
        self.release("v0.2.0", BASE, "- **x。**", files=[("new.md", "v0.2.0\n")])
        self.clone()
        pu.newer(self.p())
        write(os.path.join(self.folder(), "new.md"), "mine\n")
        c = pu.check(self.p(), "v0.2.0", "v0.3.2")
        self.assertEqual(c["kind"], "edits")
        self.assertIn("new.md", c["why"])


class Order(Rig):
    def test_requires_first_and_what_waits_on_a_failed_one(self):
        for n, reqs in (("a", ["b"]), ("b", ["c"]), ("c", []), ("d", [])):
            write(os.path.join(pu.folder_of(n), "plugin.json"), {"requires": [{"plugin": r} for r in reqs]})
        self.assertEqual(pu.order(["a", "b", "c", "d"]), ["c", "b", "a", "d"])
        self.assertEqual(pu.blocked_by(["c"], ["a", "b", "c", "d"]), ["a", "b"])
        self.assertEqual(pu.blocked_by(["d"], ["a", "b", "c", "d"]), [])


class Switch(Rig):
    def ran(self):
        with open(os.path.join(self.home, "ran.txt")) as f:
            return f.read().split()

    def test_moved_set_up_and_back_when_its_setup_fails(self):
        self.clone()
        pu.run_setup("demo", self.folder(), BASE, pu.head(self.folder()))
        self.release("v0.2.0", dict(BASE, setup=[{"name": "tools", "run": "echo two >> \"$HOME/ran.txt\""}]), "- **x。**")
        ok, why = pu.switch(self.p(), "v0.2.0")
        self.assertTrue(ok, why)
        self.assertEqual((self.at(), self.ran()), ("v0.2.0", ["tools", "two"]))
        self.release("v0.3.0", dict(BASE, setup=[{"name": "a", "run": "echo a >> \"$HOME/ran.txt\""},
                                                {"name": "b", "run": "exit 5"}]), "- **x。**")
        ok, why = pu.switch(self.p("v0.2.0"), "v0.3.0")
        self.assertFalse(ok)
        self.assertEqual(self.at(), "v0.2.0", "put back")
        self.assertIn("b（結束碼 5", why)
        self.assertIn("安裝步驟 a 已經跑過，退不回來", why)

    def test_items_left_for_later_are_no_failure(self):
        self.clone()
        self.release("v0.2.0", dict(BASE, setup=[{"name": "a", "run": "true"}, {"name": "b", "run": "true"}]), "- **x。**")
        import time
        with mock.patch.object(pu, "LAST_START", 10):  # time left to fetch, not to start a setup item
            ok, why = pu.switch(self.p(), "v0.2.0", deadline=time.time() + 5)
        self.assertTrue(ok, why)
        self.assertEqual(self.at(), "v0.2.0")
        self.assertIn("a、b 下次再裝", why)

    def test_follow_only_the_names_asked(self):
        self.clone()
        self.release("v0.2.0", BASE, "- **x。**")
        self.declare({"source": self.src, "version": "v0.2.0"})
        self.assertEqual(pu.follow(names=["other"]), [])
        self.assertEqual(self.at(), "v0.1.0")
        self.assertEqual([n for n, _, _ in pu.follow(names=["demo"])], ["demo"])

    def test_follow_takes_each_wanted_one_to_the_instances_version(self):
        self.clone()
        self.release("v0.2.0", BASE, "- **x。**")
        self.assertEqual(pu.follow(), [], "already there")
        self.declare({"source": self.src, "version": "v0.2.0"})
        got = pu.follow()
        self.assertEqual([(n, ok) for n, ok, _ in got], [("demo", True)])
        self.assertEqual(self.at(), "v0.2.0")
        self.declare({"source": self.src, "version": "v0.1.0", "disabled_on": ["box"]})
        self.assertEqual(pu.follow(), [], "not wanted here: left as it is")


class GitTimeout(unittest.TestCase):
    def test_a_git_that_does_not_end_goes_with_its_helpers_in_plain_words(self):
        """#code-review-2 of c0b20b7: a source that never answers left a clone's helpers running, and the person
        read Python's exception."""
        import time
        tmp = tempfile.mkdtemp(prefix="假 git ")
        self.addCleanup(shutil.rmtree, tmp, True)
        pidfile = os.path.join(tmp, "helper")
        write(os.path.join(tmp, "bin", "git"), "#!/bin/sh\nsleep 60 &\necho $! > '%s'\nwait\n" % pidfile)
        os.chmod(os.path.join(tmp, "bin", "git"), 0o755)
        with mock.patch.dict(os.environ, {"PATH": os.path.join(tmp, "bin") + os.pathsep + os.environ["PATH"]}):
            r = pu._git(tmp, "clone", "x", timeout=1)
        self.assertEqual((r.returncode, r.stderr), (124, "1 秒都沒有回應，已中止"))
        with open(pidfile) as f:
            pid = int(f.read())
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            os.kill(pid, 9)
            self.fail("the helper outlived git")


class Unreachable(Rig):
    def test_a_source_that_does_not_answer_within_the_round_is_said_plainly(self):
        """manager 2026-10-20 (#code-review-2 of c0b20b7): 660 s and Python's exception before."""
        import time
        p = dict(self.p(), source=os.path.join(self.home, "也沒有.git"))
        st, words, _ = pu.ensure("demo", p, deadline=time.time() - 1)
        self.assertEqual((st, words), ("failed", "連不上它的來源（%s），這輪先跳過" % p["source"]))
        self.assertIsNone(pu.newer(self.p(), deadline=time.time() - 1))
        said = []
        with mock.patch.object(pu, "_git", side_effect=lambda f, *a, **k: subprocess.CompletedProcess(a, 124, "", "60 秒都沒有回應，已中止")
                               if a[0] == "clone" else subprocess.CompletedProcess(a, 0, "", "")):
            st, words, _ = pu.ensure("demo", self.p(), log=said.append)
        self.assertTrue(words.startswith("連不上它的來源（%s），這輪先跳過（從 " % self.src), words)
        self.assertTrue(any("沒有回應" in t for t in said), "git's words in the log")
        self.assertFalse(os.path.exists(self.folder()))


class Backoff(Rig):
    """manager 2026-10-20 (#code-review-2 of e73418a: 11 minutes every round for a silent source): give up after
    SLOW_LIMIT without progress, then the rounds skip it, longer each time, until it answers."""

    def test_the_rounds_skip_a_source_that_did_not_answer_until_its_next_try(self):
        import time
        silent = dict(self.p(), source=os.path.join(self.home, "不見了.git"))
        with mock.patch.object(pu, "_network", return_value=True):
            self.assertIsNone(pu.newer(silent, backoff=True))
        (name, since, fails, nxt), = pu.unreachable_now()
        self.assertEqual((name, fails), ("demo", 1))
        self.assertAlmostEqual(nxt - time.time(), pu.BACKOFF_FIRST, delta=5)
        calls = []
        real = pu._git
        with mock.patch.object(pu, "_git", side_effect=lambda *a, **k: calls.append(a) or real(*a, **k)):
            self.assertIsNone(pu.newer(silent, backoff=True))
            self.assertEqual([a for a in calls if "fetch" in a], [], "held off: not tried")
            st, words, _ = pu.ensure("demo", silent, backoff=True)
            self.assertEqual(st, "failed")
            self.assertIn("連不上它的來源（%s），這輪先跳過（從 " % silent["source"], words)
            self.assertIn("下次", words)
        with mock.patch.object(pu, "_network", return_value=True):
            pu.newer(silent)  # a person's run tries anyway, and counts
        self.assertEqual(pu.unreachable_now()[0][2], 2)
        self.assertAlmostEqual(pu.unreachable_now()[0][3] - time.time(), 2 * pu.BACKOFF_FIRST, delta=5)
        for _ in range(10):
            with mock.patch.object(pu, "_network", return_value=True):
                pu.newer(silent)
        self.assertAlmostEqual(pu.unreachable_now()[0][3] - time.time(), pu.BACKOFF_MAX, delta=5, msg="at most 6 hours")
        self.assertEqual(pu.newer(self.p()), [], "it answers: the record goes")
        self.assertEqual(pu.unreachable_now(), [])

    def test_a_source_that_is_only_wrong_is_not_backed_off(self):
        self.assertIsNone(pu.newer(dict(self.p(), source=os.path.join(self.home, "沒有這個.git"))))
        self.assertEqual(pu.unreachable_now(), [], "not found is not the network")

    def test_a_server_that_answered_4xx_is_not_the_network(self):
        """#code-review of dc6a4ba: 403 or 404 over https said 「連不上」 and was backed off."""
        said = lambda err, rc=128: subprocess.CompletedProcess([], rc, "", err)
        self.assertFalse(pu._network(said("fatal: unable to access 'https://x/y.git/': The requested URL returned error: 403")))
        self.assertFalse(pu._network(said("fatal: unable to access 'https://x/y.git/': The requested URL returned error: 404")))
        self.assertTrue(pu._network(said("fatal: unable to access 'https://x/y.git/': Could not resolve host: x")))
        self.assertTrue(pu._network(said("", 124)))

    def test_a_fetch_that_failed_but_not_for_the_network_counts_nothing(self):
        self.clone()
        self.release("v0.2.0", BASE, "- **x。**")
        subprocess.run(["git", "-C", self.folder(), "remote", "set-url", "origin", os.path.join(self.home, "刪掉了.git")], check=True)
        p = dict(self.p(), source=os.path.join(self.home, "刪掉了.git"))
        st, _, _ = pu.ensure("demo", dict(p, version="v0.2.0"))
        self.assertEqual(st, "failed")
        self.assertEqual(pu.unreachable_now(), [], "not there is not unreachable")

    def test_git_gives_up_on_no_progress_and_ssh_has_a_connect_timeout(self):
        pu._SSH.clear()
        env = pu._git_env()
        self.assertEqual((env["GIT_HTTP_LOW_SPEED_LIMIT"], env["GIT_HTTP_LOW_SPEED_TIME"]), ("1000", str(pu.SLOW_LIMIT)))
        self.assertIn("ConnectTimeout=%d" % pu.SLOW_LIMIT, env["GIT_SSH_COMMAND"])
        pu._SSH.clear()
        with mock.patch.dict(os.environ, {"GIT_SSH_COMMAND": "ssh -i mykey"}):
            self.assertEqual(pu._git_env()["GIT_SSH_COMMAND"], "ssh -i mykey", "the person's own ssh command stays")
        pu._SSH.clear()


class SetVersions(Rig):
    def test_written_and_committed_in_the_instance(self):
        ok, why = pu.set_versions({"demo": "v0.2.0"})
        self.assertTrue(ok, why)
        with open(os.path.join(self.inst, "plugins.json")) as f:
            self.assertEqual(json.load(f)["plugins"]["demo"]["version"], "v0.2.0")
        log = subprocess.run(["git", "-C", self.inst, "log", "-1", "--format=%s"], stdout=subprocess.PIPE,
                             universal_newlines=True).stdout.strip()
        self.assertEqual(log, "plugins: demo v0.2.0")
        self.assertEqual(subprocess.run(["git", "-C", self.inst, "status", "--porcelain"], stdout=subprocess.PIPE,
                                        universal_newlines=True).stdout, "")
        self.assertFalse(pu.set_versions({"gone": "v1.0.0"})[0], "only a plugin written with a source")


if __name__ == "__main__":
    unittest.main()
