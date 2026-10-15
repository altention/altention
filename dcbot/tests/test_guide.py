"""The guide channel: GUIDE.md is cut into messages and the channel is brought in line with as few changes as possible."""
import importlib.machinery
import importlib.util
import os
import unittest
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
loader = importlib.machinery.SourceFileLoader("dcctl", os.path.join(ROOT, "tools", "dcctl"))
dcctl = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcctl", loader))
loader.exec_module(dcctl)
N = dcctl.NOTE_MARK


class Guide(unittest.TestCase):
    def test_chunks_follow_sections(self):
        self.assertEqual(dcctl.guide_chunks("# T\nintro\n\n## A\na1\na2\n\n## B\nb\n"),
                         ["# T\nintro", "## A\na1\na2", "## B\nb"])
        long = dcctl.guide_chunks("## A\n" + "\n".join("x" * 50 for _ in range(10)), limit=200)
        self.assertTrue(len(long) > 1 and all(len(c) <= 200 for c in long))

    def test_the_real_guide_fits_discord(self):
        with open(os.path.join(ROOT, "GUIDE.md"), encoding="utf-8") as f:
            chunks = dcctl.guide_chunks(f.read())
        self.assertTrue(all(0 < len(c) <= 2000 for c in chunks))
        self.assertTrue(all(not c.startswith(N) for c in chunks))
        self.assertTrue(all(c.startswith("#") for c in chunks), "every message starts at a heading")

    def test_plan(self):
        chunks = ["g1", "g2"]
        self.assertEqual(dcctl.guide_plan([], chunks, N + "first"), ([], [], ["g1", "g2", N + "first"]))
        mine = [("1", "g1"), ("2", "old"), ("3", N + "a")]
        self.assertEqual(dcctl.guide_plan(mine, chunks), ([("2", "g2")], [], []))  # edited in place
        self.assertEqual(dcctl.guide_plan(mine, chunks, N + "b"), ([("2", "g2")], [], [N + "b"]))
        self.assertEqual(dcctl.guide_plan([("1", "g1"), ("2", "g2")], chunks), ([], [], []))

    def test_plan_keeps_the_last_notices(self):
        mine = [("1", "g")] + [(str(i), N + str(i)) for i in range(2, 8)]  # six notices
        edits, deletes, posts = dcctl.guide_plan(mine, ["g"], N + "new", keep=5)
        self.assertEqual((edits, deletes, posts), ([], ["2", "3"], [N + "new"]))

    def test_plan_reposts_when_the_guide_changes_length(self):
        mine = [("1", "g1"), ("2", N + "a"), ("3", N + "b")]
        self.assertEqual(dcctl.guide_plan(mine, ["g1", "g2"], N + "c"),
                         ([], ["1", "2", "3"], ["g1", "g2", N + "a", N + "b", N + "c"]))
        out_of_order = [("1", N + "a"), ("2", "g1")]
        self.assertEqual(dcctl.guide_plan(out_of_order, ["g1"]), ([], ["1", "2"], ["g1", N + "a"]))


if __name__ == "__main__":
    unittest.main()
