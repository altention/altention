"""progress: where the repos are (instance, private engine, nowhere) and which folders say nothing about a project."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
spec = importlib.util.spec_from_file_location("progress_main", os.path.join(HERE, "..", "progress.py"))
progress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(progress)
config = progress.config


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(data if isinstance(data, str) else json.dumps(data))


def repo(path, origin=None):
    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", "-C", path, "init", "-q"], check=True)
    if origin:
        subprocess.run(["git", "-C", path, "remote", "add", "origin", origin], check=True)
    return path


GIT_ID = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
          "GIT_COMMITTER_EMAIL": "t@example.com"}


def chatlog_repo(path, commit=True):
    """chatlog's folder as its install.sh makes it (git init, branch main), with a first commit or none."""
    repo(path)
    subprocess.run(["git", "-C", path, "checkout", "-q", "-B", "main"], check=True)
    if commit:
        write(os.path.join(path, "README.md"), "logs\n")
        subprocess.run(["git", "-C", path, "add", "-A"], check=True)
        subprocess.run(["git", "-C", path, "commit", "-q", "-m", "first"], check=True, env=dict(os.environ, **GIT_ID))
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self.home = os.path.realpath(tempfile.mkdtemp())
        self.engine = repo(os.path.join(self.home, "engine"))
        self.patches = [mock.patch.dict(os.environ, {"HOME": self.home}),
                        mock.patch.object(config, "ENGINE", self.engine),
                        mock.patch.object(config, "hostname", return_value="box"),
                        mock.patch.object(progress, "_origins", {}),
                        mock.patch.object(progress, "LOGS", os.path.join(self.home, "work", "logs")),
                        mock.patch.object(progress, "PROGRESS", os.path.join(self.home, "work", "progress"))]
        for p in self.patches:
            p.start()
        for k in ("ALT_INSTANCE", "ALT_ROOT", "PROGRESS_LOGS_REMOTE", "PROGRESS_REMOTE", "PROGRESS_LOGS_WEB"):
            os.environ.pop(k, None)

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def instance(self, settings=None, machines=None, origin=None):
        inst = repo(os.path.join(self.home, "altention", "instance"), origin)
        write(os.path.join(inst, "plugins.json"), settings or {})
        if machines:
            write(os.path.join(inst, "machines.json"), {"machines": machines})
        return inst


class Remotes(Base):
    def test_nothing_set_is_nowhere(self):
        self.assertIsNone(progress.remote_of("logs"))
        self.assertIsNone(progress.remote_of("progress"))
        self.assertEqual(progress.logs_web(), "")

    def test_public_engine_gives_no_default(self):
        subprocess.run(["git", "-C", self.engine, "remote", "add", "origin", "https://github.com/alice/altention.git"])
        self.assertIsNone(progress.remote_of("logs"))

    def test_without_an_instance_the_engines_origin_says_nothing(self):
        subprocess.run(["git", "-C", self.engine, "remote", "add", "origin", "git@github.com:alice/altention-private.git"])
        self.assertIsNone(progress.remote_of("logs"))
        self.assertIsNone(progress.remote_of("progress"))

    def test_instance_decides_and_env_wins(self):
        subprocess.run(["git", "-C", self.engine, "remote", "add", "origin", "git@github.com:alice/altention-private.git"])
        self.instance({"sync": {"dirs": {"chatlog": {"remote": "git@git.example:alice/talks.git", "web": "https://git.example/alice/talks/src"},
                                         "progress": {"remote": "/srv/git/progress.git"}}}})
        self.assertEqual(progress.remote_of("logs"), "git@git.example:alice/talks.git")
        self.assertEqual(progress.remote_of("progress"), "/srv/git/progress.git")
        self.assertEqual(progress.logs_web(), "https://git.example/alice/talks/src/")
        with mock.patch.dict(os.environ, {"PROGRESS_LOGS_REMOTE": "x:y/z.git"}):
            self.assertEqual(progress.remote_of("logs"), "x:y/z.git")

    def test_other_names_of_the_settings_are_not_read(self):  # only sync.dirs.chatlog and sync.dirs.progress
        self.instance({"sync": {"dirs": {"logs": {"remote": "git@git.example:alice/old-talks.git", "web": "https://old/"},
                                         "manager": {"remote": "/srv/git/other.git"}}}})
        self.assertIsNone(progress.remote_of("logs"))
        self.assertIsNone(progress.remote_of("progress"))
        self.assertEqual(progress.logs_web(), "")

    def test_instance_without_remotes_is_local_only(self):
        subprocess.run(["git", "-C", self.engine, "remote", "add", "origin", "git@github.com:alice/altention-private.git"])
        self.instance({})
        self.assertIsNone(progress.remote_of("progress"))

    def test_links_follow_an_existing_mirror_and_are_left_out_for_unknown_hosts(self):
        repo(progress.LOGS, "git@github.com:alice/old-name.git")
        self.assertEqual(progress.logs_web(), "https://github.com/alice/old-name/tree/main/")
        with mock.patch.dict(os.environ, {"PROGRESS_LOGS_REMOTE": "git@git.example:alice/talks.git"}):
            self.assertEqual(progress.logs_web(), "")

    def test_entry_without_links_and_local_only_repo(self):
        with mock.patch.object(progress.cl, "REPO", chatlog_repo(os.path.join(self.home, "data", "logs"))):
            progress.ensure_repos()
        self.assertTrue(os.path.exists(os.path.join(progress.PROGRESS, "README.md")))
        self.assertNotIn("](", progress.read(os.path.join(progress.PROGRESS, "README.md")).split("\n")[2])
        when = progress.now_utc()
        progress.write_entry("p", {"host": "host-a"}, ["host-a/2026-10/x-12345678"], {"summary": "s"}, "", when)
        text = progress.read(os.path.join(progress.PROGRESS, "projects", "p", "log", when.astimezone(progress.TZ).strftime("%Y-%m") + ".md"))
        self.assertIn("`host-a/2026-10/x-12345678`", text)

    def test_no_logs_and_no_remote_says_what_to_set(self):
        with self.assertRaises(SystemExit) as e:
            progress.ensure_repos()
        self.assertIn("sync.dirs.chatlog.remote", str(e.exception))


class Folders(Base):
    def test_homes_come_from_this_machine_and_the_list(self):
        with mock.patch.dict(os.environ, {"HOME": "/data/u/alice"}):
            self.assertTrue(progress.is_generic_dir("/data/u/alice"))
            self.assertFalse(progress.is_generic_dir("/data/u/alice/proj"))
        self.assertTrue(progress.is_generic_dir("/home/alice"))
        self.assertTrue(progress.is_generic_dir("/tmp/x"))
        self.assertFalse(progress.is_generic_dir("/data/share/alice"))
        write(os.path.join(self.engine, "ops", "machines.json"),
              {"machines": {"host-a": {"home": "/data/share/alice"}, "host-b": {}}})
        self.assertFalse(progress.is_generic_dir("/data/share/alice/"), "an old list in the engine is not read")
        self.instance({"progress": {"home_dirs": ["/srv/u/alice"]}}, {"host-c": {"home": "/vol/alice"}})
        self.assertTrue(progress.is_generic_dir("/vol/alice"))
        self.assertTrue(progress.is_generic_dir("/srv/u/alice"))
        self.assertFalse(progress.is_generic_dir("/data/share/alice"), "the instance's list replaces the old one")

    def test_learned_paths_skip_homes(self):
        self.instance({}, {"host-a": {"home": "/data/share/alice"}})
        p = {}
        self.assertFalse(progress.learn_identity(p, {"cwd": "/data/share/alice"}))
        self.assertTrue(progress.learn_identity(p, {"cwd": "/data/share/alice/proj"}))


class InstanceSessions(Base):
    PROJECTS = {"sys": {"id": "sys", "remotes": ["git@github.com:alice/altention-private.git"], "paths": []},
                "other": {"id": "other", "remotes": ["git@github.com:alice/other.git"], "paths": ["/data/other"]}}

    def setUp(self):
        Base.setUp(self)
        subprocess.run(["git", "-C", self.engine, "remote", "add", "origin", "git@github.com:alice/altention-private.git"])

    def test_no_instance_nothing_changes(self):
        self.assertEqual(progress.match_project({"cwd": "/data/other/x"}, self.PROJECTS), ("other", "path"))
        self.assertEqual(progress.match_project({"cwd": "/data/else"}, self.PROJECTS), (None, None))
        self.assertEqual(progress.match_project({"git": {"remote": "https://github.com/alice/my-altention"}, "cwd": "/data/else"},
                                          self.PROJECTS), (None, None))

    def test_by_folder_here_and_on_other_machines(self):
        inst = self.instance({}, {"host-a": {"home": "/data/share/alice"}})
        self.assertEqual(progress.match_project({"cwd": inst}, self.PROJECTS), ("sys", "instance"))
        self.assertEqual(progress.match_project({"cwd": inst + "/knowledge"}, self.PROJECTS), ("sys", "instance"))
        self.assertEqual(progress.match_project({"cwd": "/data/share/alice/altention/instance"}, self.PROJECTS), ("sys", "instance"))
        self.assertEqual(progress.match_project({"cwd": inst + "-old"}, self.PROJECTS), (None, None))

    def test_by_remote_and_known_rules_win(self):
        self.instance({}, origin="git@github.com:alice/my-altention.git")
        meta = {"git": {"remote": "https://github.com/alice/my-altention"}, "cwd": "/somewhere/else"}
        self.assertEqual(progress.match_project(meta, self.PROJECTS), ("sys", "instance"))
        projects = dict(self.PROJECTS, mine={"id": "mine", "remotes": ["git@github.com:alice/my-altention.git"]})
        self.assertEqual(progress.match_project(meta, projects), ("mine", "remote"))

    def test_which_project_is_the_engine(self):
        inst = self.instance({"progress": {"instance_project": "other"}})
        self.assertEqual(progress.match_project({"cwd": inst}, self.PROJECTS), ("other", "instance"))
        write(os.path.join(inst, "plugins.json"), {})
        renamed = {"x": {"id": "x", "remotes": ["git@github.com:alice/before-rename.git"], "paths": [self.engine]}}
        self.assertEqual(progress.match_project({"cwd": inst}, renamed), ("x", "instance"), "by the engine's folder")
        named = {"engine": {"id": "engine", "remotes": []}}
        self.assertEqual(progress.match_project({"cwd": inst}, named), ("engine", "instance"), "by the folder's name")
        self.assertEqual(progress.match_project({"cwd": inst}, {"y": {"id": "y"}}), (None, None))
        origin = "git@github.com:alice/altention-private.git"
        several = {"a": {"id": "a", "remotes": [origin]}, "b": {"id": "b", "remotes": [origin]}}
        self.assertEqual(progress.match_project({"cwd": inst}, several), (None, None), "several know the remote: no guess")
        several["altention-private"] = {"id": "altention-private", "remotes": []}
        self.assertEqual(progress.match_project({"cwd": inst}, several), ("altention-private", "instance"), "by the repo's name")



class SharedRemote(Base):
    """Several projects knowing one remote: the same answer every round, whatever order the folders come in."""
    ORIGIN = "git@github.com:alice/tool.git"

    def projects(self, order):
        made = {"zeta": {"id": "zeta", "remotes": [self.ORIGIN]}, "alpha": {"id": "alpha", "remotes": [self.ORIGIN]},
                "mid": {"id": "mid", "remotes": [self.ORIGIN]}}
        return {k: made[k] for k in order}

    def test_first_by_id_in_any_order(self):
        meta = {"git": {"remote": "https://github.com/alice/tool"}, "cwd": "/x"}
        for order in (["zeta", "alpha", "mid"], ["mid", "zeta", "alpha"], ["alpha", "mid", "zeta"]):
            self.assertEqual(progress.match_project(meta, self.projects(order)), ("alpha", "remote"), order)

    def test_the_instances_project_when_it_is_one_of_them(self):
        self.instance({"progress": {"instance_project": "mid"}})
        meta = {"git": {"remote": self.ORIGIN}, "cwd": "/x"}
        self.assertEqual(progress.match_project(meta, self.projects(["zeta", "alpha", "mid"])), ("mid", "remote"))
        self.instance({"progress": {"instance_project": "elsewhere"}})
        self.assertEqual(progress.match_project(meta, self.projects(["zeta", "mid"])), ("mid", "remote"), "not one of them")

    def test_equal_paths_in_any_order(self):
        made = {"b": {"id": "b", "paths": ["/w/tool"]}, "a": {"id": "a", "paths": ["/w/tool"]}}
        for order in (["b", "a"], ["a", "b"]):
            self.assertEqual(progress.match_project({"cwd": "/w/tool/src"}, {k: made[k] for k in order}), ("a", "path"))


class Aliases(Base):
    """A name the answer gives that is an old name of a project goes to that project, never a new one."""
    PROJECTS = {"altention": {"id": "altention", "aliases": ["agent-home"]}, "life": {"id": "life", "aliases": []},
                "_misc": {"id": "_misc"}}

    def test_ids_and_their_slugs_first(self):
        self.assertEqual(progress.resolve_project("life", self.PROJECTS), "life")
        self.assertEqual(progress.resolve_project("Life", self.PROJECTS), "life")
        self.assertEqual(progress.resolve_project(None, self.PROJECTS), "_misc")
        self.assertEqual(progress.resolve_project("", self.PROJECTS), "_misc")

    def test_an_old_name_joins_the_renamed_project(self):
        for said in ("agent-home", "Agent Home", "AGENT_HOME"):
            self.assertEqual(progress.resolve_project(said, self.PROJECTS), "altention", said)

    def test_an_unknown_name_is_a_new_project(self):
        self.assertEqual(progress.resolve_project("New Thing", self.PROJECTS), "new-thing")
        self.assertEqual(progress.resolve_project("agent", self.PROJECTS), "agent", "a part of an alias is not it")

    def test_the_instances_table(self):
        self.instance({"progress": {"aliases": {"old-tool": "life", "gone": "no-such-project", "x": 3}}})
        plain = {"life": {"id": "life"}}
        self.assertEqual(progress.resolve_project("Old Tool", plain), "life")
        self.assertEqual(progress.resolve_project("gone", plain), "gone", "a table entry to a missing project is not used")
        self.instance({"progress": {"aliases": ["not", "a", "table"]}})
        self.assertEqual(progress.resolve_project("old-tool", plain), "old-tool")


class BackgroundClaude(unittest.TestCase):
    def test_the_claude_it_asks_is_marked_as_a_background_one(self):
        """altention/background.py (stage 3b step 1.9b): the mark every part knows, not a name of one plugin's hook."""
        seen = {}

        def run(cmd, **kw):
            seen.update(cmd=cmd, env=kw.get("env"))
            return mock.Mock(returncode=0, stdout=json.dumps({"structured_output": {"ok": 1}, "total_cost_usd": 0.5}), stderr="")

        with mock.patch.object(progress.subprocess, "run", run), mock.patch.dict(os.environ, {"KEPT": "1"}):
            os.environ.pop("ALTENTION_BACKGROUND", None)
            self.assertEqual(progress.ask("system", "prompt", {"type": "object"}, "haiku"), ({"ok": 1}, 0.5))
            self.assertNotIn("ALTENTION_BACKGROUND", os.environ, "the mark is the helper's, not this process's own")
        self.assertEqual((seen["env"].get("ALTENTION_BACKGROUND"), seen["env"].get("KEPT"), "CHATLOG_SKIP" in seen["env"]), ("1", "1", False))
        self.assertIn("--no-session-persistence", seen["cmd"])
        with open(os.path.join(HERE, "..", "progress.py"), encoding="utf-8") as f:
            self.assertNotIn("CHATLOG_SKIP", f.read())


if __name__ == "__main__":
    unittest.main()
