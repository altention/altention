"""C-76: what "已上線，版本 `abc1234`（…）" says in brackets: the release when HEAD is one, else HEAD's own subject."""
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from bot import main
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)


class VersionNote(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="version note ")
        self.addCleanup(shutil.rmtree, self.repo, True)
        self.env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
                        GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        self.git("init", "-q")
        os.makedirs(os.path.join(self.repo, "dcbot"))
        self.commit("bot change", "dcbot/a.py")
        patch = mock.patch.object(main.config, "ROOT", self.repo)
        patch.start()
        self.addCleanup(patch.stop)

    def git(self, *args):
        subprocess.run(["git", "-C", self.repo] + list(args), check=True, env=self.env, stdout=subprocess.DEVNULL)

    def commit(self, subject, path):
        with open(os.path.join(self.repo, path), "a") as f:
            f.write("x\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", subject)

    def test_heads_own_subject_not_the_last_bot_commit(self):
        self.commit("release: merge release/x (CHANGELOG heading)", "CHANGELOG.md")
        self.assertEqual(main.version_note(), "release: merge release/x (CHANGELOG heading)")

    def test_a_release_says_its_version(self):
        self.commit("CHANGELOG: v0.3.10 heading", "CHANGELOG.md")
        for tag in ("v0.3.9", "v0.3.10", "keep-this", "v1.0"):
            self.git("tag", tag)
        self.assertEqual(main.version_note(), "v0.3.10", "the highest release tag on HEAD; other tags are not releases")

    def test_a_tag_on_an_older_commit_is_not_heads(self):
        self.git("tag", "v0.3.1")
        self.commit("after the release", "dcbot/b.py")
        self.assertEqual(main.version_note(), "after the release")


if __name__ == "__main__":
    unittest.main()
