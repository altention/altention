"""tools/privacy-scan: general patterns, the instance's blacklist, made-up values, the tree (not the folder),
file names, the settings list, and the exit status.

Everything that looks personal here is put together at run time, so this file passes the scan itself."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
loader = importlib.machinery.SourceFileLoader("privacy_scan", os.path.join(os.path.dirname(HERE), "privacy-scan"))
spec = importlib.util.spec_from_loader("privacy_scan", loader)
ps = importlib.util.module_from_spec(spec)
loader.exec_module(ps)

J = "".join
REAL_ID = J(["1556", "3372", "7212", "3456", "789"])        # 19 digits, not of the made-up shape
REAL_IP = ".".join(["140", "82", "112", "3"])
REAL_MAIL = J(["someone", "@", "corp-mail", ".io"])
REAL_HOME = J(["/ho", "me/", "zoltan"])
REAL_OWNER = J(["github", ".com/", "zoltan-dev/", "private-engine"])
THIRD = J(["github", ".com/", "thirdparty/", "tool"])
MACHINE, ALIAS, ACCOUNT = "zebrabox7", "zebraboxP", "zoltan"
AUTHOR_NAME, AUTHOR_MAIL = "Zoltan Example-Person", J(["zoltan", "@", "corp-mail", ".io"])


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False))


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.repo = tempfile.mkdtemp()
        self.engine = tempfile.mkdtemp()  # stands for the engine's old places: empty unless a test fills it
        self.env = {"HOME": self.home, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": AUTHOR_NAME,
                    "GIT_AUTHOR_EMAIL": AUTHOR_MAIL, "GIT_COMMITTER_NAME": AUTHOR_NAME,
                    "GIT_COMMITTER_EMAIL": AUTHOR_MAIL}
        self.patches = [mock.patch.dict(os.environ, self.env), mock.patch.object(ps.config, "ENGINE", self.engine)]
        for p in self.patches:
            p.start()
        for k in ("ALT_INSTANCE", "ALT_ROOT"):
            os.environ.pop(k, None)
        self.git("init", "-q")

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def git(self, *args):
        return subprocess.run(["git", "-C", self.repo] + list(args), check=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE).stdout.decode().strip()

    def commit(self, files, message="add"):
        for path, data in files.items():
            write(os.path.join(self.repo, path), data)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def instance(self, extra=None):
        inst = os.path.join(self.home, "altention", "instance")
        write(os.path.join(inst, "machines.json"), {"machines": {MACHINE: {
            "ssh": [MACHINE, ALIAS], "hostname": MACHINE + "-lan", "home": J(["/srv/", "people/", ACCOUNT])}}})
        write(os.path.join(inst, "ssh_config"), "Host %s\n  HostName %s\n  User %s\nHost *\n  Compression yes\n"
              % (ALIAS, REAL_IP, ACCOUNT))
        if extra is not None:
            write(os.path.join(inst, "privacy.json"), extra)
        return inst

    def run_scan(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = ps.main(["--repo", self.repo] + list(args))
        return code, out.getvalue()

    def findings(self, *args):
        code, out = self.run_scan("--json", *args)
        data = json.loads(out)
        self.assertEqual(data["exit"], code)
        return code, data


class GeneralPatterns(Base):
    def test_each_pattern_is_found_with_file_line_and_half_hidden(self):
        self.commit({"a/notes.txt": "\n".join(["nothing here", "id " + REAL_ID, "at " + REAL_IP, "mail " + REAL_MAIL,
                                                "cwd " + REAL_HOME + "/x", "see https://" + REAL_OWNER]) + "\n"})
        code, data = self.findings()
        self.assertEqual(code, ps.FOUND)
        got = {(f["rule"], f["line"]) for f in data["findings"]}
        self.assertEqual(got, {("discord-id", 2), ("ipv4", 3), ("email", 4), ("home-path", 5), ("github-owner", 6)})
        for f in data["findings"]:
            self.assertEqual(f["path"], "a/notes.txt")
            self.assertIn("*", f["snippet"])
        for whole in (REAL_ID, REAL_IP, REAL_MAIL, REAL_HOME, REAL_OWNER):
            self.assertNotIn(whole, json.dumps(data))
        code, text = self.run_scan()
        self.assertIn("a/notes.txt:2  discord-id  " + REAL_ID[:10] + "*" * 9, text)
        self.assertIn("沒有黑名單", text)

    def test_made_up_values_pass(self):
        self.commit({"ok.md": "\n".join([
            "channel 100000000000000001 and 100000000000000042", "host-a talks to host-b as alice",
            "ssh alice@host-a", "/home/alice/altention and /Users/alice and /home/u/p and /home/<you>/x",
            "192.0.2.10 198.51.100.7 203.0.113.9 127.0.0.1 0.0.0.0", "noreply@anthropic.com alice@example.com",
            "git@github.com:alice/altention.git https://github.com/<you>/my-altention",
            "version 3.12.300.1 and 0.12345678901234567890 and sha 4cf2b3947512345678901234567890abcdef1234",
            "${REMOTE:-git@github.com:alice/x.git}"]) + "\n"})
        code, data = self.findings()
        self.assertEqual((code, data["findings"]), (ps.CLEAN, []))

    def test_public_repo_and_allowed_owner_pass(self):
        self.commit({"README.md": "clone https://%s.git\nuses https://%s\n" % (REAL_OWNER, THIRD)})
        self.assertEqual(self.findings()[1]["summary"]["personal"]["total"], 2)
        code, data = self.findings("--public-repo", REAL_OWNER.split("/", 1)[1], "--allow-owner", "thirdparty")
        self.assertEqual((code, data["findings"]), (ps.CLEAN, []))


class Blacklist(Base):
    def test_terms_come_from_the_instance(self):
        self.instance({"terms": ["Project Nightjar"], "allow_authors": []})
        self.commit({"run.sh": "ssh %s uptime\nscp x %s:\nchown %s f\n# for project nightjar\ncd %s\n"
                     % (MACHINE.upper(), ALIAS, ACCOUNT, J(["/srv/", "people/", ACCOUNT]))})
        code, data = self.findings()
        self.assertEqual(code, ps.FOUND)
        self.assertEqual(data["blacklist"]["source"], "instance")
        self.assertEqual([(f["line"], f["rule"]) for f in data["findings"]],
                         [(1, "machine"), (2, "ssh-alias"), (3, "account"), (4, "instance-term"), (5, "machine-home")])

    def test_whole_words_only_and_ordinary_names_are_left_out(self):
        inst = self.instance()
        write(os.path.join(inst, "machines.json"), {"machines": {"mac": {"ssh": ["local"]}, MACHINE: {"ssh": [MACHINE]}}})
        self.commit({"t.txt": "on macOS a mac is local\n%sful and x%s\n" % (MACHINE, MACHINE)})
        code, data = self.findings()
        self.assertEqual((code, data["findings"]), (ps.CLEAN, []))
        self.assertTrue(data["blacklist"]["notes"])

    def test_authors_and_remotes_of_the_private_repo(self):
        self.instance()
        self.git("remote", "add", "origin", J(["git@", "github", ".com:", "zoltan-dev/", "private-engine.git"]))
        self.commit({"AUTHORS": "written by %s\nmirror of zoltan-dev/private-engine\n" % AUTHOR_NAME})
        code, data = self.findings()
        self.assertEqual([(f["line"], f["rule"]) for f in data["findings"]], [(1, "author"), (2, "private-repo")])

    def test_public_repo_owner_may_be_the_account(self):
        self.instance()
        public = ACCOUNT + "/altention"
        self.commit({"README.md": "git clone https://github.com/%s\nmade by %s\n" % (public, ACCOUNT)})
        code, data = self.findings("--public-repo", public)
        self.assertEqual([(f["line"], f["rule"]) for f in data["findings"]], [(2, "account")])

    def test_an_old_machine_list_in_the_engine_is_not_read(self):
        write(os.path.join(self.engine, "ops", "machines.json"), {"machines": {MACHINE: {"ssh": [MACHINE]}}})
        self.commit({"x": "fine\n"})
        code, data = self.findings()
        self.assertEqual((code, data["blacklist"]["source"]), (ps.CLEAN, "none"))
        self.assertEqual(self.run_scan("--require-blacklist")[0], ps.NO_BLACKLIST)
        self.instance({"terms": ["Some Lab"]})
        self.assertEqual(self.run_scan("--require-blacklist")[0], ps.CLEAN)


class ExitStatus(Base):
    def test_no_instance_runs_general_patterns_and_says_so(self):
        self.commit({"x": "run on %s\n" % MACHINE})
        code, text = self.run_scan()
        self.assertEqual(code, ps.CLEAN)
        self.assertIn("沒有黑名單", text)

    def test_require_blacklist_without_one_is_its_own_status(self):
        self.commit({"x": "id " + REAL_ID + "\n"})
        code, data = self.findings("--require-blacklist")
        self.assertEqual(code, ps.NO_BLACKLIST)
        self.assertFalse(data["blacklist"]["usable"])
        self.assertEqual(data["summary"]["personal"]["total"], 1)  # still reported

    def test_instance_with_nothing_in_it_is_no_blacklist(self):
        os.makedirs(os.path.join(self.home, "altention", "instance"))
        self.commit({"x": "fine\n"})
        self.assertEqual(self.run_scan("--require-blacklist")[0], ps.NO_BLACKLIST)
        self.instance()  # machines and accounts, but no privacy.json: not enough to publish
        self.assertEqual(self.run_scan("--require-blacklist")[0], ps.NO_BLACKLIST)
        self.instance({"terms": []})  # a privacy.json without terms: not enough either
        self.assertEqual(self.run_scan("--require-blacklist")[0], ps.NO_BLACKLIST)
        self.instance({"terms": ["Some Lab"]})
        self.assertEqual(self.run_scan("--require-blacklist")[0], ps.CLEAN)

    def test_bad_revision_and_bad_usage(self):
        self.commit({"x": "fine\n"})
        self.assertEqual(self.run_scan("no-such-rev")[0], ps.FAILED)
        self.assertEqual(self.run_scan("--settings", "--all")[0], ps.FAILED)


class Tree(Base):
    def test_only_what_is_committed(self):
        first = self.commit({"old.txt": "id " + REAL_ID + "\n"})
        self.git("rm", "-q", "old.txt")
        second = self.commit({"new.txt": "fine\n"}, "replace")
        write(os.path.join(self.repo, "draft.txt"), "at " + REAL_IP + "\n")       # never committed
        write(os.path.join(self.repo, "new.txt"), "mail " + REAL_MAIL + "\n")     # changed, not committed
        self.assertEqual(self.run_scan()[0], ps.CLEAN)
        self.assertEqual(self.run_scan(second)[0], ps.CLEAN)
        self.assertEqual(self.run_scan(first)[0], ps.FOUND)
        self.assertEqual(self.run_scan(self.git("rev-parse", first + "^{tree}"))[0], ps.FOUND)

    def test_file_names_binary_files_and_links(self):
        self.instance()
        with open(os.path.join(self.repo, "blob.bin"), "wb") as f:
            f.write(b"\0\1\2 " + REAL_ID.encode())
        os.symlink(REAL_HOME + "/target", os.path.join(self.repo, "link"))
        self.commit({"runs/2026-%s-plan.md" % MACHINE: "fine\n", "說明 文件.md": "fine\n"})
        code, data = self.findings()
        self.assertEqual(sorted((f["path"], f["line"], f["rule"]) for f in data["findings"]),
                         [("link", 1, "home-path"), ("runs/2026-%s-plan.md" % MACHINE, 0, "machine")])
        self.assertEqual(data["skipped"], [{"path": "blob.bin", "why": "binary"}])
        self.assertIn("(檔名)", self.run_scan()[1])

    def test_commit_message_and_author_with_meta(self):
        rev = self.commit({"x": "fine\n"}, "fix the thing on " + REAL_IP)
        self.assertEqual(self.run_scan(rev)[0], ps.CLEAN)
        code, data = self.findings(rev, "--meta")
        self.assertEqual(sorted((f["path"], f["rule"]) for f in data["findings"]),
                         [("<commit>", "email"), ("<commit>", "email"), ("<commit>", "ipv4")])

    def test_path_and_exclude(self):
        self.commit({"keep/a": "id " + REAL_ID + "\n", "drop/b": "id " + REAL_ID + "\n", "dropped": "id " + REAL_ID + "\n"})
        self.assertEqual(self.findings("--exclude", "drop")[1]["summary"]["personal"]["files"], 2)
        self.assertEqual(self.findings("--path", "keep")[1]["summary"]["personal"]["files"], 1)


class Settings(Base):
    TEXT = "\n".join(['TZ = "%s"' % J(["Asia", "/", "Tokyo"]), "dcpost %s hello" % J(["日常", "/", "用量"]),
                      "貼到 %s" % J(["#", "秘書"]), "cd ~/%s" % J(["agent", "-", "home"]), "用量達到上限"]) + "\n"

    def test_settings_are_a_separate_list_and_never_change_the_status(self):
        self.commit({"conf.py": self.TEXT})
        self.assertEqual(self.run_scan()[0], ps.CLEAN)
        code, data = self.findings("--settings")
        self.assertEqual(code, ps.CLEAN)
        self.assertNotIn("personal", data["summary"])
        self.assertEqual([(f["line"], f["rule"]) for f in data["findings"]],
                         [(1, "timezone"), (2, "cjk-channel"), (3, "cjk-channel"), (4, "legacy-name")])

    def test_all_reports_both_apart(self):
        self.commit({"conf.py": self.TEXT + "id " + REAL_ID + "\n"})
        code, data = self.findings("--all", "--summary")
        self.assertEqual(code, ps.FOUND)
        self.assertEqual((data["summary"]["personal"]["rules"], data["summary"]["settings"]["total"]),
                         ({"discord-id": 1}, 4))
        self.assertEqual(data["findings"], [])
        text = self.run_scan("--all")[1]
        self.assertLess(text.index("== 個人資訊"), text.index("== 寫死的實例設定"))

    def test_instance_channel_names_where_used_as_channels(self):
        self.instance({"channels": ["wishes", "ops-room", "用量"], "categories": ["daily"]})
        names = os.path.join(self.home, "names.txt")
        write(names, "#extra-room\n")
        self.commit({"doc.md": "post to #wishes or daily/ops-room\nthree wishes daily\ndcpost extra-room hi\n"
                               'FAIL = "用量達到上限"\n'})
        code, data = self.findings("--settings", "--channels-file", names)
        self.assertEqual([(f["line"], f["rule"]) for f in data["findings"]],
                         [(1, "channel-name"), (1, "channel-name"), (3, "channel-name")])


class OwnFiles(unittest.TestCase):
    def test_this_tool_and_its_tests_pass_the_general_patterns(self):
        rules = ps.general_rules()
        for path in (os.path.join(os.path.dirname(HERE), "privacy-scan"), os.path.abspath(__file__).replace(".pyc", ".py")):
            with open(path, encoding="utf-8") as f:
                for n, line in enumerate(f, 1):
                    self.assertEqual(ps.scan_text(line, rules), [], "%s:%d" % (path, n))


if __name__ == "__main__":
    unittest.main()
