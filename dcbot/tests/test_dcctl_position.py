"""dcctl position (manager 2026-10-20: the owner cannot drag categories on a tablet): a category among the categories, a
channel among the channels of its own category and kind, Discord's bulk position update, the system category kept at
the bottom when another category moves, and every change in #audit."""
import importlib.machinery
import importlib.util
import os
import unittest
from unittest import mock

from bot import config
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def dcctl():
    loader = importlib.machinery.SourceFileLoader("dcctl_pos", os.path.join(ROOT, "tools", "dcctl"))
    spec = importlib.util.spec_from_loader("dcctl_pos", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


SYS = "系統"


def server():
    cats = [("1", "機器人", 0), ("2", "proj", 1), ("3", "插件：learn", 2), ("4", "插件：latex", 3), ("5", SYS, 4)]
    chans = [{"id": i, "name": n, "type": 4, "position": p} for i, n, p in cats]
    chans += [{"id": "11", "name": "chat", "type": 0, "position": 0, "parent_id": None},
              {"id": "12", "name": "a", "type": 0, "position": 0, "parent_id": "2"},
              {"id": "13", "name": "b", "type": 0, "position": 1, "parent_id": "2"},
              {"id": "14", "name": "c", "type": 0, "position": 2, "parent_id": "2"},
              {"id": "15", "name": "talk", "type": 2, "position": 0, "parent_id": "2"},
              {"id": "16", "name": "audit", "type": 0, "position": 0, "parent_id": "5"}]
    return chans


class Plan(unittest.TestCase):
    def setUp(self):
        self.d = dcctl()

    def plan(self, what, how, other=None, chans=None):
        return self.d.position_plan(chans or server(), what, how, other, SYS)

    def test_the_owners_two_plugin_categories_above_the_bots(self):
        chans = server()
        payload, names, note = self.plan("插件：learn", "above", "機器人", chans)
        self.assertEqual(names, ["插件：learn", "機器人", "proj", "插件：latex", SYS])
        self.assertEqual(payload, [{"id": i, "position": n} for n, i in enumerate(["3", "1", "2", "4", "5"])])
        for p in payload:  # as Discord applies it
            next(c for c in chans if c["id"] == p["id"])["position"] = p["position"]
        _, names, _ = self.plan("插件：latex", "above", "機器人", chans)
        self.assertEqual(names, ["插件：learn", "插件：latex", "機器人", "proj", SYS])

    def test_the_system_category_stays_last_when_another_moves(self):
        _, names, note = self.plan("proj", "bottom")
        self.assertEqual(names[-2:], ["proj", SYS])
        self.assertIn("維持在最下面", note)
        _, names, note = self.plan("機器人", "below", SYS)
        self.assertEqual(names[-1], SYS)
        _, names, note = self.plan(SYS, "top")
        self.assertEqual((names[0], note), (SYS, ""), "moved itself, by name: as asked")

    def test_a_system_category_the_owner_put_above_stays_there(self):
        """Manager 2026-10-20: the owner's layout has the system category above, between dividers; moving another
        category to the bottom leaves it where it is."""
        chans = server()
        for c in chans:
            if c["type"] == 4:
                c["position"] = {"1": 0, "5": 1, "2": 2, "3": 3, "4": 4}[c["id"]]
        _, names, note = self.plan("proj", "bottom", chans=chans)
        self.assertEqual((names, note), (["機器人", SYS, "插件：learn", "插件：latex", "proj"], ""))
        _, names, note = self.plan("插件：latex", "above", "機器人", chans=chans)
        self.assertEqual(names[:2], ["插件：latex", "機器人"])
        self.assertEqual(names.index(SYS), 2, "moves along with the order above it, not to the bottom")

    def test_a_channel_among_its_own_category_and_kind(self):
        payload, names, _ = self.plan("c", "above", "a")
        self.assertEqual(names, ["c", "a", "b"], "the voice channel is not in the row")
        self.assertEqual([p["id"] for p in payload], ["14", "12", "13"])
        _, names, _ = self.plan("a", "bottom")
        self.assertEqual(names, ["b", "c", "a"])
        for what, other in (("a", "chat"), ("a", "talk"), ("a", "proj"), ("proj", "a")):
            with self.assertRaises(ValueError, msg=(what, other)) as e:
                self.plan(what, "above", other)
            self.assertIn("不在同一層", str(e.exception))

    def test_category_slash_name_when_two_categories_have_a_channel_of_that_name(self):
        """#code-review of 0e0d4b9: "category/name" was looked up in a list without the categories and never matched."""
        chans = server() + [{"id": "21", "name": "notes", "type": 0, "position": 3, "parent_id": "2"},
                            {"id": "22", "name": "notes", "type": 0, "position": 0, "parent_id": "3"},
                            {"id": "23", "name": "todo", "type": 0, "position": 1, "parent_id": "3"}]
        payload, names, _ = self.plan("proj/notes", "above", "proj/a", chans)
        self.assertEqual(names, ["notes", "a", "b", "c"])
        self.assertEqual(payload[0]["id"], "21", "the one in proj, not the other notes")
        _, names, _ = self.plan("插件：learn/todo", "above", "插件：learn/notes", chans)
        self.assertEqual(names, ["todo", "notes"])
        with self.assertRaises(ValueError) as e:
            self.plan("notes", "top", chans=chans)
        self.assertIn("有 2 個頻道叫 #notes", str(e.exception), "the plain name is not guessed")
        with self.assertRaises(ValueError) as e:
            self.plan("proj/notes", "above", "插件：learn/todo", chans)
        self.assertIn("不在同一層", str(e.exception))

    def test_names_not_guessed(self):
        chans = server() + [{"id": "17", "name": "proj", "type": 0, "position": 3, "parent_id": "2"}]
        with self.assertRaises(ValueError) as e:
            self.plan("proj", "top", chans=chans)
        self.assertIn("同時是分類和頻道", str(e.exception))
        self.assertEqual(self.plan("2", "top", chans=chans)[1][0], "proj", "by its ID it is plain")
        for bad in (("nope", "top", None), ("a", "above", "nope"), ("a", "above", "a"), ("a", "sideways", None)):
            with self.assertRaises(ValueError, msg=bad):
                self.plan(*bad)


class Command(unittest.TestCase):
    def test_one_bulk_update_and_a_line_in_audit(self):
        d = dcctl()
        calls, said = [], []
        with mock.patch.object(d, "channels", server), mock.patch.object(d, "call", lambda m, p, b=None: calls.append((m, p, b))), \
                mock.patch.object(d, "audit", said.append), mock.patch.object(d.config, "SYSTEM_CATEGORY", SYS), \
                mock.patch.object(d.config, "GUILD_ID", 42), mock.patch("builtins.print"):
            d.main(["position", "插件：learn", "--above", "機器人"])
        (method, path, body), = calls
        self.assertEqual((method, path), ("PATCH", "/guilds/42/channels"))
        self.assertEqual([p["id"] for p in body], ["3", "1", "2", "4", "5"])
        self.assertIn("插件：learn > 機器人", said[0])
        for argv in (["position"], ["position", "proj"], ["position", "--top"], ["position", "nope", "--top"]):
            with mock.patch.object(d, "channels", server), mock.patch.object(d, "call", lambda *a: self.fail("called")), \
                    self.assertRaises(SystemExit, msg=argv):
                d.main(argv)
