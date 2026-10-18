"""ops/remote.py against throwaway repos, a throwaway HOME and a small HTTP server on 127.0.0.1 (never GitHub)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import base64  # noqa: E402
import http.server  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import socket  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import unittest  # noqa: E402
from contextlib import redirect_stdout  # noqa: E402
from unittest import mock  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import remote  # noqa: E402

GOOD = "Basic " + base64.b64encode(b"alice:s3cret").decode()


def sh(cwd, *args, env=None):
    return subprocess.run(list(args), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                          env=env)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def bare_with(tmp, name, files):
    """A bare repo at tmp/name.git with one commit holding `files` (none: an empty repo), served over dumb HTTP."""
    bare = os.path.join(tmp, name + ".git")
    os.makedirs(tmp, exist_ok=True)
    sh(tmp, "git", "init", "-q", "--bare", bare)
    sh(bare, "git", "symbolic-ref", "HEAD", "refs/heads/main")
    if files:
        work = os.path.join(tmp, name + ".work")
        sh(tmp, "git", "clone", "-q", bare, work)
        sh(work, "git", "checkout", "-q", "-b", "main")
        for f in files:
            write(os.path.join(work, f), "x\n")
        sh(work, "git", "add", "-A")
        sh(work, "git", "commit", "-qm", "init")
        sh(work, "git", "push", "-q", "origin", "main")
    sh(bare, "git", "update-server-info")
    return bare


class Server:
    """Serves `root` over dumb HTTP; locked=True: 401 unless the request carries alice's password."""

    def __init__(self, root, locked):
        outer = self

        class H(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=root, **k)

            def do_GET(self):
                if outer.locked and self.headers.get("Authorization") != GOOD:
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="r"')
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                super().do_GET()

            def log_message(self, *a):
                pass

        self.locked = locked
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, name):
        return "http://127.0.0.1:%d/%s.git" % (self.port, name)

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="遠端 ")
        os.environ.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                           "GIT_COMMITTER_EMAIL": "t@example.com"})
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        # this user's own login for the locked server, the way gh auth setup-git leaves one: a credential helper
        write(os.path.join(self.home, ".gitconfig"),
              "[credential]\n\thelper = \"!f() { echo username=alice; echo password=s3cret; }; f\"\n")
        self.env = mock.patch.dict(os.environ, {"HOME": self.home})
        self.env.start()
        self.eng = mock.patch.object(remote, "engine_urls", return_value=["git@github.com:alice/altention.git"])
        self.eng.start()

    def tearDown(self):
        self.eng.stop()
        self.env.stop()
        shutil.rmtree(self.tmp)


class Urls(unittest.TestCase):
    def test_the_engines_urls_are_read_whole(self):
        out = subprocess.CompletedProcess([], 0, "origin\t/srv/my engine.git (fetch)\norigin\t/srv/my engine.git (push)\n"
                                          "up\tgit@example.com:alice/altention.git (fetch)\n", "")
        with mock.patch.object(remote, "git", return_value=out):
            self.assertEqual(remote.engine_urls(), ["/srv/my engine.git", "git@example.com:alice/altention.git"])

    def test_spellings_of_one_repo_are_the_same(self):
        same = ["git@example.com:Alice/Data.git", "https://example.com/alice/data",
                "ssh://git@example.com:22/alice/data.git/", "https://alice:tok@example.com/alice/data.git"]
        for u in same[1:]:
            self.assertTrue(remote.same_repo(same[0], u), u)
        self.assertFalse(remote.same_repo("git@example.com:alice/data.git", "git@example.com:alice/data2.git"))
        self.assertFalse(remote.same_repo("git@example.com:alice/data.git", "git@example.org:alice/data.git"))

    def test_anyone_reads_it_by_https_with_no_user_in_it(self):
        self.assertEqual(remote.anonymous_form("git@example.com:alice/data.git"), ("https://example.com/alice/data.git", "from-ssh"))
        self.assertEqual(remote.anonymous_form("ssh://git@host.example:2222/srv/data.git"),
                         ("https://host.example/srv/data.git", "from-ssh"))
        self.assertEqual(remote.anonymous_form("https://alice:tok@example.com/alice/data.git"),
                         ("https://example.com/alice/data.git", "as-is"))
        self.assertEqual(remote.anonymous_form("/srv/data.git"), ("", "local"))


class Anonymous(Base):
    def setUp(self):
        super().setUp()
        self.root = os.path.join(self.tmp, "srv")
        os.makedirs(self.root)
        bare_with(self.root, "data", ["plugins.json"])

    def serve(self, locked):
        s = Server(self.root, locked)
        self.addCleanup(s.close)
        return s

    def test_readable_with_no_login_is_public(self):
        s = self.serve(locked=False)
        self.assertEqual(remote.anonymous(s.url("data"))[0], "public")

    def test_a_login_this_user_has_is_not_used(self):
        s = self.serve(locked=True)
        self.assertEqual(sh(self.tmp, "git", "ls-remote", s.url("data")).returncode, 0, "this user can read it")
        verdict, why = remote.anonymous(s.url("data"))
        self.assertEqual(verdict, "private", why)

    def test_the_repo_at_hand_lends_the_read_no_login(self):
        """Review of a6d3ffe (b): the hook runs inside the data repo; its own .git/config (url.*.insteadOf to a
        URL with a login, http.extraheader) must not make a private repo look public."""
        s = self.serve(locked=True)
        repo = os.path.join(self.tmp, "data-repo")
        sh(self.tmp, "git", "init", "-q", repo)
        base = "http://127.0.0.1:%d/" % s.port
        login = "alice:" + "s3cret"  # (in two parts: the privacy scan reads user@host as an address)
        sh(repo, "git", "config", "url.http://" + login + "@" + "127.0.0.1:%d/.insteadOf" % s.port, base)
        sh(repo, "git", "config", "http.extraheader", "Authorization: " + GOOD)
        self.assertEqual(sh(repo, "git", "-c", "credential.helper=", "ls-remote", s.url("data"),
                            env=dict(os.environ, HOME=os.path.join(self.tmp, "nobody"))).returncode, 0, "the repo lends a login")
        here = os.getcwd()
        os.chdir(repo)
        try:
            verdict, why = remote.anonymous(s.url("data"))
        finally:
            os.chdir(here)
        self.assertEqual(verdict, "private", why)

    def test_not_reachable_cannot_be_told(self):
        verdict, why = remote.anonymous("http://127.0.0.1:%d/data.git" % closed_port())
        self.assertEqual(verdict, "unknown")
        self.assertIn("沒辦法確認", why)
        verdict, why = remote.anonymous("git@127.0.0.1:data.git")  # https on 127.0.0.1:443: nobody there
        self.assertEqual(verdict, "unknown")
        self.assertIn("SSH", why)

    def test_github_s_own_answer_counts(self):
        down = subprocess.CompletedProcess([], 128, "", "fatal: unable to access: Could not resolve host: github.com")
        with mock.patch.object(remote, "run", return_value=down):
            with mock.patch.object(remote, "github_public", return_value="public"):
                self.assertEqual(remote.anonymous("git@github.com:alice/data.git")[0], "public")
            with mock.patch.object(remote, "github_public", return_value="private"):
                self.assertEqual(remote.anonymous("git@github.com:alice/data.git")[0], "private")
            with mock.patch.object(remote, "github_public", return_value=""):
                self.assertEqual(remote.anonymous("git@github.com:alice/data.git")[0], "unknown")
        readable = subprocess.CompletedProcess([], 0, "abc\tHEAD\n", "")
        with mock.patch.object(remote, "run", return_value=readable), \
                mock.patch.object(remote, "github_public", return_value="private"):
            self.assertEqual(remote.anonymous("git@github.com:alice/data.git")[0], "public", "read is read")


class Check(Base):
    def statuses(self, url, name):
        return {item: s for item, s, _ in remote.check(url, name)}

    def test_the_engines_repo_is_refused_before_anything_is_asked(self):
        with mock.patch.object(remote, "anonymous") as anon:
            got = remote.check("https://github.com/alice/altention", "instance")
        self.assertEqual([(i, s) for i, s, _ in got], [("engine", "no")])
        anon.assert_not_called()

    def test_a_url_with_a_token_in_it_is_refused(self):
        with mock.patch.object(remote, "anonymous") as anon:
            got = remote.check("https://alice:tok@example.com/alice/data.git", "logs")
        self.assertEqual([(i, s) for i, s, _ in got], [("secret", "no")])
        anon.assert_not_called()
        for url in ("https://tok123@example.com/alice/data.git", "https://x-access-token@example.com/alice/data.git",
                    "http://alice@127.0.0.1:%d/x.git" % closed_port()):  # a token takes the user's place on GitHub
            self.assertEqual([(i, s) for i, s, _ in remote.check(url, "logs")], [("secret", "no")], url)
        with mock.patch.object(remote, "anonymous", return_value=("public", "x")):  # stops there: no network
            for url in ("git@example.com:alice/data.git", "ssh://git@example.com/alice/data.git"):  # ssh: the user is no secret
                self.assertEqual([i for i, _s, _ in remote.check(url, "logs")], ["engine", "anonymous"], url)

    def test_empty_or_the_same_kind_passes(self):
        empty = bare_with(self.tmp, "empty", [])
        self.assertEqual(self.statuses(empty, "logs"), {"engine": "ok", "anonymous": "ok", "content": "ok", "cron": "ok"})
        inst = bare_with(self.tmp, "inst", ["plugins.json", "knowledge/a.md"])
        self.assertTrue(remote.passed(remote.check(inst, "instance")))
        logs = bare_with(self.tmp, "logs", ["host-a/2026-10/abc/meta.json"])
        self.assertTrue(remote.passed(remote.check(logs, "logs")))
        self.assertFalse(remote.passed(remote.check(logs, "instance")), "logs are not an instance")

    def test_an_engines_tree_or_other_data_is_refused(self):
        eng = bare_with(self.tmp, "fork", ["altention/sync.py", "plugins.json"])
        self.assertEqual(self.statuses(eng, "instance")["content"], "no", "a fork of the engine, whatever else it holds")
        other = bare_with(self.tmp, "other", ["src/main.c"])
        got = remote.check(other, "progress")
        self.assertEqual(got[-1][:2], ("content", "no"))
        self.assertIn("STATUS.md", got[-1][2])

    def test_public_is_refused_and_nothing_after_is_asked(self):
        root = os.path.join(self.tmp, "srv")
        bare_with(root, "pub", [])
        s = Server(root, locked=False)
        self.addCleanup(s.close)
        got = remote.check(s.url("pub"), "logs")
        self.assertEqual([(i, st) for i, st, _ in got], [("engine", "ok"), ("anonymous", "no")])
        self.assertIn("公開", got[-1][2])

    def test_private_over_http_with_this_users_login_passes_and_cannot_tell_does_too(self):
        root = os.path.join(self.tmp, "srv")
        bare_with(root, "priv", [])
        s = Server(root, locked=True)
        self.addCleanup(s.close)
        self.assertEqual(self.statuses(s.url("priv"), "logs"),
                         {"engine": "ok", "anonymous": "ok", "content": "ok", "cron": "ok"})
        with mock.patch.object(remote, "anonymous", return_value=("unknown", "沒辦法確認：offline")):
            got = remote.check(bare_with(self.tmp, "e2", []), "logs")
        self.assertEqual(got[1][1], "unsure")
        self.assertTrue(remote.passed(got), "cannot tell is no refusal (decision 6a)")

    def test_not_reachable_the_way_cron_runs_is_refused(self):
        root = os.path.join(self.tmp, "srv")
        bare_with(root, "priv", [])
        s = Server(root, locked=True)
        self.addCleanup(s.close)
        bare_env = dict(remote.cron_env(), HOME=os.path.join(self.tmp, "no-login"))  # the login only in a terminal
        with mock.patch.object(remote, "cron_env", return_value=bare_env):
            got = remote.check(s.url("priv"), "logs")
        self.assertEqual(got[-1][:2], ("cron", "no"))
        self.assertIn("gh auth setup-git", got[-1][2])

    def test_a_wrong_url_says_so(self):
        got = remote.check(os.path.join(self.tmp, "nothing.git"), "logs")
        self.assertEqual(got[-1][:2], ("content", "no"))


class Said(unittest.TestCase):
    def test_the_cause_is_kept_not_gits_last_line(self):
        """#code-review-2 on 6ec93eb: an SSH failure read "（and the repository exists.）"."""
        ssh = subprocess.CompletedProcess([], 128, "", "git@example.com: Permission denied (publickey).\r\n"
                                          "fatal: Could not read from remote repository.\n\n"
                                          "Please make sure you have the correct access rights\nand the repository exists.\n")
        text = remote.said(ssh)
        self.assertIn("Permission denied (publickey)", text)
        self.assertIn("Could not read from remote repository", text)
        self.assertNotIn("and the repository exists", text)
        self.assertEqual(remote.said(subprocess.CompletedProcess([], 2, "", "")), "rc=2")

    def test_every_message_that_quotes_git_says_the_cause(self):
        ssh = subprocess.CompletedProcess([], 128, "", "fatal: Could not read from remote repository.\n\n"
                                          "Please make sure you have the correct access rights\nand the repository exists.\n")
        with mock.patch.object(remote, "run", return_value=ssh):
            files, problem = remote.tree_of("git@example.com:alice/data.git")
            self.assertIsNone(files)
            self.assertIn("Could not read from remote repository", problem)
            verdict, why = remote.anonymous("ssh://git@example.com/alice/data.git")
            self.assertIn("Could not read from remote repository", why)


class Attach(Base):
    def setUp(self):
        super().setUp()
        self.repo = os.path.join(self.tmp, "logs")
        write(os.path.join(self.repo, "host-a", "x", "meta.json"), "{}\n")
        sh(self.repo, "git", "init", "-q")
        self.alerts = mock.patch.object(remote, "alert")
        self.alert = self.alerts.start()

    def tearDown(self):
        self.alerts.stop()
        super().tearDown()

    def test_no_repo_or_nothing_set_does_nothing(self):
        self.assertEqual(remote.attach("logs", "x", os.path.join(self.tmp, "none"))[0], "none")
        self.assertEqual(remote.attach("logs", "", self.repo)[0], "none")
        self.assertFalse(remote.has_hook(self.repo))

    def test_no_origin_gets_the_setting_checked_and_the_hook(self):
        bare = bare_with(self.tmp, "logs-remote", [])
        what, text = remote.attach("logs", bare, self.repo)
        self.assertEqual(what, "set", text)
        self.assertEqual(remote.origin(self.repo), bare)
        self.assertTrue(remote.has_hook(self.repo))
        self.assertEqual(remote.attach("logs", bare, self.repo)[0], "same", "again: as it is")
        self.alert.assert_not_called()

    def test_an_origin_already_there_gets_the_hook_another_one_is_left(self):
        bare, other = bare_with(self.tmp, "a", []), bare_with(self.tmp, "b", [])
        sh(self.repo, "git", "remote", "add", "origin", bare)
        self.assertEqual(remote.attach("logs", "", self.repo)[0], "same", "no setting: its own origin is left")
        self.assertTrue(remote.has_hook(self.repo))
        what, text = remote.attach("logs", other, self.repo)
        self.assertEqual(what, "differs")
        self.assertIn("--replace", text)
        self.assertEqual(remote.origin(self.repo), bare)
        self.assertEqual(self.alert.call_args[0][0], "origin-differs")

    def test_a_setting_that_fails_the_check_is_not_taken(self):
        with mock.patch.object(remote, "anonymous", return_value=("public", "anyone")):
            what, text = remote.attach("logs", bare_with(self.tmp, "pub", []), self.repo)
        self.assertEqual(what, "refused")
        self.assertIn("公開", text)
        self.assertEqual(remote.origin(self.repo), "")
        self.assertFalse(remote.has_hook(self.repo))
        self.assertEqual(self.alert.call_args[0][0], "attach-refused")

    def test_from_the_command_line(self):
        bare = bare_with(self.tmp, "cli", [])
        with mock.patch.object(sys, "stdout", new_callable=__import__("io").StringIO) as out, \
                mock.patch("altention.engine_id.refusal", return_value=""):
            self.assertEqual(remote.main(["attach", "logs", bare, self.repo]), 0)
        self.assertIn("接上", out.getvalue())
        self.assertEqual(remote.origin(self.repo), bare)


class SetRemote(Base):
    def setUp(self):
        super().setUp()
        self.inst = os.path.join(self.tmp, "instance")
        write(os.path.join(self.inst, "plugins.json"), json.dumps(
            {"format": 1, "core": {"timezone": "UTC"}, "sync": {"interval": "15m", "dirs": {"progress": {"web": "w"}}}},
            ensure_ascii=False, indent=2) + "\n")
        sh(self.inst, "git", "init", "-q")
        self.logs = os.path.join(self.tmp, "data", "logs")
        write(os.path.join(self.logs, "host-a", "x", "meta.json"), "{}\n")
        sh(self.logs, "git", "init", "-q")
        sh(self.logs, "git", "checkout", "-q", "-b", "main")
        sh(self.logs, "git", "add", "-A")
        sh(self.logs, "git", "commit", "-qm", "logs")
        self.env2 = mock.patch.dict(os.environ, {"ALT_INSTANCE": self.inst, "CHATLOG_REPO": self.logs})
        self.env2.start()
        self.said = []

    def tearDown(self):
        self.env2.stop()
        super().tearDown()

    def set(self, name, url, replace=False):
        return remote.set_remote(name, url, replace, say=self.said.append)

    def settings(self):
        with open(os.path.join(self.inst, "plugins.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_written_down_given_its_origin_hooked_and_pushed(self):
        bare = bare_with(self.tmp, "logs-remote", [])
        self.assertEqual(self.set("logs", bare), 0, self.said)
        d = self.settings()
        self.assertEqual(d["sync"]["dirs"]["chatlog"], {"remote": bare})
        self.assertEqual((d["sync"]["dirs"]["progress"], d["core"]["timezone"], d["sync"]["interval"]), ({"web": "w"}, "UTC", "15m"))
        self.assertEqual(remote.origin(self.logs), bare)
        self.assertTrue(remote.has_hook(self.logs))
        self.assertEqual(sh(bare, "git", "rev-parse", "main").stdout, sh(self.logs, "git", "rev-parse", "HEAD").stdout)
        self.assertEqual(sh(self.logs, "git", "rev-parse", "--abbrev-ref", "@{u}").stdout.strip(), "origin/main")
        self.assertEqual(self.set("logs", bare), 0, "again: nothing new, no harm")

    def test_another_origin_or_setting_is_left_unless_replace(self):
        bare, other = bare_with(self.tmp, "a", []), bare_with(self.tmp, "b", [])
        sh(self.logs, "git", "remote", "add", "origin", other)
        self.assertEqual(self.set("logs", bare), 1)
        self.assertIn("origin 已經是", self.said[-1])
        self.assertEqual(remote.origin(self.logs), other)
        self.assertEqual(self.set("logs", bare, replace=True), 0, self.said)
        self.assertEqual(remote.origin(self.logs), bare)
        third = bare_with(self.tmp, "c", [])
        self.assertEqual(self.set("logs", third), 1)
        self.assertIn("sync.dirs.chatlog.remote 已經是", self.said[-1])
        self.assertEqual(self.settings()["sync"]["dirs"]["chatlog"]["remote"], bare)

    def test_a_check_that_fails_writes_nothing(self):
        before = self.settings()
        with mock.patch.object(remote, "anonymous", return_value=("public", "anyone")):
            self.assertEqual(self.set("logs", bare_with(self.tmp, "pub", [])), 1)
        self.assertEqual(self.settings(), before)
        self.assertEqual(remote.origin(self.logs), "")
        self.assertFalse(remote.has_hook(self.logs))

    def test_a_folder_not_there_yet_gets_only_the_setting(self):
        shutil.rmtree(self.logs)
        bare = bare_with(self.tmp, "later", [])
        self.assertEqual(self.set("logs", bare), 0, self.said)
        self.assertEqual(self.settings()["sync"]["dirs"]["chatlog"]["remote"], bare)
        self.assertFalse(os.path.exists(self.logs))

    def test_the_instance_gets_its_origin_not_a_setting(self):
        write(os.path.join(self.inst, "machines.json"), "{}\n")
        sh(self.inst, "git", "add", "-A")
        sh(self.inst, "git", "commit", "-qm", "i")
        before = self.settings()
        bare = bare_with(self.tmp, "inst", [])
        self.assertEqual(self.set("instance", bare), 0, self.said)
        self.assertEqual(self.settings(), before)
        self.assertEqual(remote.origin(self.inst), bare)

    def test_status_says_each_one(self):
        bare = bare_with(self.tmp, "logs-remote", [])
        self.set("logs", bare)
        rows = {r["name"]: r for r in remote.status()}
        self.assertEqual((rows["logs"]["origin"], rows["logs"]["hook"], rows["logs"]["ahead"]), (bare, True, 0))
        self.assertIsNotNone(rows["logs"]["last_push"])
        self.assertEqual(rows["logs"]["check"]["verdict"], "private")
        self.assertEqual((rows["instance"]["repo"], rows["instance"]["origin"]), (True, ""))
        self.assertFalse(rows["progress"]["repo"])


class Hook(Base):
    def setUp(self):
        super().setUp()
        self.repo = os.path.join(self.tmp, "data")
        write(os.path.join(self.repo, "plan.md"), "p\n")
        sh(self.repo, "git", "init", "-q")
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "commit", "-qm", "p")
        # an alert just told: the hook's alert is written down without the bot being asked to tell it again
        write(os.path.join(self.home, ".local", "state", "altention", "alerts-told.json"),
              json.dumps({"remote/public": time.time(), "remote/engine-url": time.time()}))

    def push(self, url):
        sh(self.repo, "git", "remote", "add", "origin", url)
        return sh(self.repo, "git", "push", "-q", "origin", "HEAD:main", env=dict(os.environ))

    def test_a_public_repo_is_never_pushed_to_and_it_is_written_down(self):
        root = os.path.join(self.tmp, "srv")
        bare_with(root, "pub", [])
        s = Server(root, locked=False)
        self.addCleanup(s.close)
        self.assertEqual(remote.put_hook(self.repo)[0], True)
        r = self.push(s.url("pub"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("公開的 repo", r.stderr)
        with open(os.path.join(self.home, ".local", "state", "altention", "alerts"), encoding="utf-8") as f:
            rows = [line.rstrip("\n").split("\t", 3) for line in f]
        self.assertEqual([r[1:3] for r in rows], [["remote", "public"]])
        self.assertIn(s.url("pub"), rows[0][3])

    def test_private_goes_through_and_the_answer_is_kept_but_never_public(self):
        bare = bare_with(self.tmp, "priv", [])
        remote.put_hook(self.repo)
        self.assertEqual(self.push(bare).returncode, 0)
        self.assertEqual(remote.recent(bare)[0], "private")
        remote.remember(bare, "public", "x")
        self.assertIsNone(remote.recent(bare), "public is asked again every time")
        remote.remember(bare, "private", "x")
        d = remote.read_checks()
        d[remote.key_of(bare)]["at"] = time.time() - remote.KEEP - 1
        with open(remote.checks_path(), "w") as f:
            json.dump(d, f)
        self.assertIsNone(remote.recent(bare), "an hour old: asked again")

    def test_the_engines_repo_is_never_pushed_to(self):
        with mock.patch.object(remote, "anonymous") as anon, mock.patch.object(remote, "alert"):
            self.assertEqual(remote.pre_push("origin", "https://github.com/alice/altention.git"), remote.STOP)
        anon.assert_not_called()

    def test_another_hook_is_left_as_it_is(self):
        hp = remote.hook_path(self.repo)
        write(hp, "#!/bin/sh\nexit 0\n")
        ok, text = remote.put_hook(self.repo)
        self.assertFalse(ok)
        self.assertIn("別的 pre-push hook", text)
        with open(hp) as f:
            self.assertEqual(f.read(), "#!/bin/sh\nexit 0\n")

    def test_a_shared_hooks_folder_gets_nothing(self):
        """Review of a6d3ffe (c): core.hooksPath set for every repo of this user: ours there would check every push
        (the engine's own was stopped)."""
        shared = os.path.join(self.tmp, "shared-hooks")
        os.makedirs(shared)
        with open(os.path.join(self.home, ".gitconfig"), "a") as f:
            f.write("[core]\n\thooksPath = %s\n" % shared)
        ok, text = remote.put_hook(self.repo)
        self.assertFalse(ok)
        self.assertIn("core.hooksPath", text)
        self.assertEqual(os.listdir(shared), [])
        self.assertFalse(remote.has_hook(self.repo))
        with open(os.path.join(self.home, ".gitconfig"), "w") as f:
            f.write("")
        self.assertTrue(remote.put_hook(self.repo)[0], "its own folder: put in")

    def test_ours_is_refreshed_and_the_engine_gone_lets_the_push_go_saying_so(self):
        hp = remote.hook_path(self.repo)
        write(hp, "#!/bin/sh\n%s\nexit 1\n" % remote.HOOK_MARK)
        self.assertEqual(remote.put_hook(self.repo), (True, "放好 pre-push hook"))
        with open(hp) as f:
            self.assertEqual(f.read(), remote.hook_text())
        self.assertTrue(os.access(hp, os.X_OK))
        with open(hp, "w") as f:
            f.write(remote.hook_text().replace(os.path.join(remote.ENGINE, "ops", "remote.py"), "/nowhere/remote.py"))
        r = self.push(bare_with(self.tmp, "priv", []))
        self.assertEqual(r.returncode, 0)
        self.assertIn("was not checked", r.stderr)
        self.assertEqual([row[1:3] for row in self.alerts()], [["remote", "hook-broken"]])

    def alerts(self):
        try:
            with open(os.path.join(self.home, ".local", "state", "altention", "alerts"), encoding="utf-8") as f:
                return [line.rstrip("\n").split("\t", 3) for line in f]
        except OSError:
            return []

    def test_a_remote_py_that_fails_lets_the_push_go_saying_so(self):
        """Manager 2026-10-16: a fault of the check (remote.py cannot be imported, it raises) never stops the
        owner's backups: said on stderr and in the alerts, pushed."""
        broken = os.path.join(self.tmp, "broken", "remote.py")
        write(broken, "raise ImportError('altention/ cannot be read')\n")
        write(remote.hook_path(self.repo), remote.hook_text().replace(remote.shlex.quote(os.path.join(remote.ENGINE, "ops", "remote.py")),
                                                                 remote.shlex.quote(broken)))
        os.chmod(remote.hook_path(self.repo), 0o755)
        bare = bare_with(self.tmp, "priv", [])
        r = self.push(bare)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("failed (exit 1), this push was not checked", r.stderr)
        self.assertEqual(sh(bare, "git", "rev-parse", "main").returncode, 0, "pushed")
        rows = self.alerts()
        self.assertEqual([row[1:3] for row in rows], [["remote", "hook-broken"]])
        self.assertIn("沒有檢查就放行", rows[0][3])

    def pushing_setup(self):
        bare = bare_with(self.tmp, "priv", [])
        remote.put_hook(self.repo)
        sh(self.repo, "git", "remote", "add", "origin", bare)
        return bare

    def test_a_push_from_a_linked_worktree_is_this_repos(self):
        """Review of cbc6d25: git hands the hook GIT_DIR (<repo>/.git/worktrees/<name>) there; remote.py read the
        data repo's own remote as the engine's and stopped the push ("是引擎的 repo")."""
        bare = self.pushing_setup()
        wt = os.path.join(self.tmp, "wt")
        sh(self.repo, "git", "worktree", "add", "-q", "-b", "side", wt)
        r = sh(wt, "git", "push", "-q", "origin", "side", env=dict(os.environ))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sh(bare, "git", "rev-parse", "side").returncode, 0, "pushed from the worktree")
        self.assertEqual(self.alerts(), [])
        root = os.path.join(self.tmp, "srv")
        bare_with(root, "pub", [])
        s = Server(root, locked=False)
        self.addCleanup(s.close)
        sh(self.repo, "git", "remote", "set-url", "origin", s.url("pub"))
        r = sh(wt, "git", "push", "-q", "origin", "side", env=dict(os.environ))
        self.assertNotEqual(r.returncode, 0, "a public one is still stopped from the worktree")
        self.assertIn("公開的 repo", r.stderr)

    def test_a_push_with_git_dir_set_is_this_repos(self):
        """Review of cbc6d25: GIT_DIR=<abs> git push (or --git-dir), from anywhere. In a folder with no space in
        its name: the old reading of `git remote -v` cut such a URL at the space and so never met the fault."""
        plain = tempfile.mkdtemp(prefix="gitdir")
        self.addCleanup(shutil.rmtree, plain)
        self.repo = os.path.join(plain, "data")
        write(os.path.join(self.repo, "plan.md"), "p\n")
        sh(plain, "git", "init", "-q", self.repo)
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "commit", "-qm", "p")
        bare = bare_with(plain, "priv", [])
        remote.put_hook(self.repo)
        sh(self.repo, "git", "remote", "add", "origin", bare)
        env = dict(os.environ, GIT_DIR=os.path.join(self.repo, ".git"), GIT_WORK_TREE=self.repo)
        r = sh(self.tmp, "git", "push", "-q", "origin", "HEAD:main", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sh(bare, "git", "rev-parse", "main").returncode, 0, "pushed with GIT_DIR")
        r = sh(self.tmp, "git", "--git-dir=" + os.path.join(self.repo, ".git"), "push", "-q", "origin", "HEAD:other",
               env=dict(os.environ))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.alerts(), [])
    def test_the_alerts_name_the_repo_pushed_not_the_folder_typed_in(self):
        """Review of d1d71c8: GIT_DIR=<abs> git push from /tmp was written down as "/tmp：沒有推…"."""
        root = os.path.join(self.tmp, "srv")
        bare_with(root, "pub", [])
        s = Server(root, locked=False)
        self.addCleanup(s.close)
        remote.put_hook(self.repo)
        sh(self.repo, "git", "remote", "add", "origin", s.url("pub"))
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.makedirs(elsewhere)
        r = sh(elsewhere, "git", "push", "-q", "origin", "HEAD:main", env=dict(os.environ, GIT_DIR=os.path.join(self.repo, ".git")))
        self.assertNotEqual(r.returncode, 0)
        wt = os.path.join(self.tmp, "wt")
        sh(self.repo, "git", "worktree", "add", "-q", "-b", "side", wt)
        r = sh(elsewhere, "git", "--git-dir=" + os.path.join(self.repo, ".git", "worktrees", "wt"), "push", "-q", "origin", "side",
               env=dict(os.environ))
        self.assertNotEqual(r.returncode, 0)
        rows = self.alerts()
        self.assertEqual([row[1:3] for row in rows], [["remote", "public"]], "one line, said twice")
        self.assertTrue(rows[0][3].startswith(wt + "："), rows[0][3])  # the last one told
        # the hook's own line when remote.py fails: the same
        broken = os.path.join(self.tmp, "broken", "remote.py")
        write(broken, "raise ImportError('altention/ cannot be read')\n")
        write(remote.hook_path(self.repo), remote.hook_text().replace(
            remote.shlex.quote(os.path.join(remote.ENGINE, "ops", "remote.py")), remote.shlex.quote(broken)))
        bare = bare_with(self.tmp, "priv", [])
        sh(self.repo, "git", "remote", "set-url", "origin", bare)
        for env in (dict(os.environ, GIT_DIR=os.path.join(self.repo, ".git")),
                    dict(os.environ, GIT_DIR=os.path.join(self.repo, ".git", "worktrees", "wt"))):
            r = sh(elsewhere, "git", "push", "-q", "origin", "HEAD:main" if "worktrees" not in env["GIT_DIR"] else "side", env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
        broke = [row[3] for row in self.alerts() if row[2] == "hook-broken"]
        self.assertEqual([t.split("：")[0] for t in broke], [self.repo, wt])

    def test_pushing_repo_from_the_git_dir(self):
        self.assertEqual(remote.repo_of_git_dir("/srv/a b/.git"), "/srv/a b")
        self.assertEqual(remote.repo_of_git_dir("/srv/bare.git"), "/srv/bare.git")
        self.assertEqual(remote.repo_of_git_dir("/srv/x/.git", "/srv/tree"), "/srv/tree")
        self.assertEqual(remote.repo_of_git_dir(""), "")


class Clone(Base):
    """Review of d1d71c8: a new machine's folder was cloned from the setting unchecked, then attached (which only
    checks a repo with no origin): a setting naming another data repo, or the engine's, was taken."""

    def test_only_what_passes_the_checks_is_cloned(self):
        dest = os.path.join(self.tmp, "logs")
        bare = bare_with(self.tmp, "planner-backup", ["plan.md"])
        with mock.patch.object(remote, "alert") as told:
            ok, text = remote.clone("logs", bare, dest)
        self.assertFalse(ok)
        self.assertIn("沒有從設定的遠端", text)
        self.assertFalse(os.path.exists(dest), "nothing cloned")
        self.assertEqual([c.args[0] for c in told.call_args_list], ["clone-refused"])
        engine_like = bare_with(self.tmp, "eng", ["altention/sync.py"])
        with mock.patch.object(remote, "alert"):
            self.assertFalse(remote.clone("planner", engine_like, dest)[0])
        self.assertFalse(os.path.exists(dest))
        ok, text = remote.clone("planner", bare, dest)
        self.assertTrue(ok, text)
        self.assertTrue(os.path.isfile(os.path.join(dest, "plan.md")))

    def shaped(self, name, branches, head="refs/heads/master"):
        """A bare backup holding plan.md on each of `branches` (none: empty), its HEAD naming `head`."""
        bare = bare_with(self.tmp, name, ["plan.md"] if branches else [])
        work = os.path.join(self.tmp, name + ".work")
        sh(bare, "git", "symbolic-ref", "HEAD", head)
        if branches:
            sh(work, "git", "push", "-q", "origin", *["main:%s" % b for b in branches if b != "main"])
            if "main" not in branches:
                sh(work, "git", "push", "-q", "origin", ":main")
        return bare

    def test_a_head_naming_no_branch_is_checked_out_from_main_or_the_only_one(self):
        """C-43: `git init --bare` says master, the data is on another branch: the clone came back an empty branch
        (chatlog's and progress' too). main, or the only branch, is checked out tracking origin; the words say
        which, and for the records and progress that main is where it goes next."""
        cases = [("has-main", ["main", "dev"], "main", "（main）"),
                 ("only-master-like", ["mine"], "mine", "遠端原本的分支是 mine，之後推到 main"),
                 ("several", ["a", "b"], None, "認不出哪個分支是備份（有 a、b，沒有 main）"),
                 ("empty", [], None, "裡面沒有內容（空的 repo）")]
        for label, branches, want, words in cases:
            for name in ("logs", "planner"):
                dest = os.path.join(self.tmp, "dest-%s-%s" % (label, name))
                bare = self.shaped("%s-%s" % (label, name), branches)
                if name == "logs" and branches:
                    with mock.patch.object(remote, "tree_of", return_value=(["host-a/s1/meta.json"], None)):
                        ok, text = remote.clone(name, bare, dest)
                else:
                    ok, text = remote.clone(name, bare, dest)
                self.assertTrue(ok, (label, name, text))
                if name == "planner" and want == "mine":
                    self.assertIn("（mine）", text, "the secretary stays on that branch: nothing said of main")
                else:
                    self.assertIn(words, text, (label, name))
                head = sh(dest, "git", "rev-parse", "--abbrev-ref", "@{u}").stdout.strip()
                self.assertEqual(head, "origin/%s" % want if want else "@{u}", (label, name))
                if want:
                    self.assertTrue(os.path.isfile(os.path.join(dest, "plan.md")), (label, name))
                left = sh(bare, "git", "for-each-ref", "--format=%(refname:short)").stdout.split()
                self.assertEqual(sorted(left), sorted(branches), "the remote's branches as they were")

    def test_checked_out_again_says_the_same(self):
        dest = os.path.join(self.tmp, "again")
        bare = self.shaped("again", ["mine"])
        self.assertTrue(remote.clone("planner", bare, dest)[0])
        self.assertEqual(remote.checkout_backup(dest), ("mine", []))

    def test_status_says_where_the_records_went_from_another_branch(self):
        row = {"name": "logs", "branch": "main", "remote_branches": ["main", "master"]}
        self.assertEqual(remote.other_branches(row), ["master"])
        self.assertEqual(remote.other_branches(dict(row, name="planner")), [], "the secretary stays on its branch")
        self.assertEqual(remote.other_branches(dict(row, remote_branches=["main"])), [])
        out = io.StringIO()
        rows = [{"name": "logs", "folder": "/x", "repo": True, "origin": "git@example.com:alice/logs.git", "hook": True,
                 "ahead": 0, "last_push": None, "check": None, "branch": "main", "remote_branches": ["master", "main"]}]
        with redirect_stdout(out):
            remote.show(rows)
        self.assertIn("遠端原本的分支是 master，之後推到 main", out.getvalue())

    def test_the_command_says_it_and_exits_1_when_refused(self):
        dest = os.path.join(self.tmp, "logs")
        bare = bare_with(self.tmp, "other", ["notes.md"])
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(remote, "alert"), \
                mock.patch("altention.engine_id.refusal", return_value=None):
            self.assertEqual(remote.main(["clone", "logs", bare, dest]), 1)
        self.assertTrue(out.getvalue().startswith("⚠ "), out.getvalue())
        self.assertFalse(os.path.exists(dest))


if __name__ == "__main__":
    unittest.main()
