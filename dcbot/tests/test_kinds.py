"""What a channel is, said in one place (bot/kinds.py, stage 3b step 2.5): the kind, the connector, archived and
why; and that the two places which each worked it out for themselves (posts.listens, dcctl's kind_of) answer as
they did."""
import ast
import importlib.machinery
import importlib.util
import itertools
import json
import os
import unittest
from unittest import mock

from bot import archive, config, kinds, main, posts
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_loader = importlib.machinery.SourceFileLoader("dcctl_kinds", os.path.join(ROOT, "tools", "dcctl"))
dcctl = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcctl_kinds", _loader))
_loader.exec_module(dcctl)


def listens_as_it_was(st):
    return bool(st) and not st.get("plain") and not st.get("archived") and st.get("kind") != "terminal" and \
        bool(st.get("tmux") or st.get("pane") or st.get("home") or st.get("session_id"))


def kind_of_as_it_was(c, st):
    st = st or {}
    if c["name"] == config.GUIDE_CHANNEL:
        return "📖", "使用說明（機器人不回應）"
    if st.get("archived") or not st and c.get("_category") == config.ARCHIVE_CATEGORY:
        return "🗄️", "封存"
    if st.get("kind") == "terminal":
        return "💻", "終端機"
    if st.get("plain") or c["name"] in (config.AUDIT_CHANNEL, "notifications"):
        return "📝", "一般頻道（紀錄用，機器人不回應）"
    if st.get("tmux") or st.get("home") or st.get("pane") or st.get("session_id"):
        where = (st.get("pane") or {}).get("host")
        cwd = (st.get("cwd") or "").replace(os.path.expanduser("~"), "~")
        return "🤖", "Claude 對話，工作目錄 %s%s" % (cwd or "?", "（在 %s）" % where if where else "")
    return "❔", "還沒設定"


def every_state():
    """Every combination of what a state can hold that bears on its kind, as states the bot writes them (a terminal
    channel has a home, a files channel has none of a conversation's keys), stamped or not."""
    keys = [("plain", True), ("archived", {"pane": {"id": "%1"}}), ("tmux", "dc-x-1"), ("pane", {"socket": "/s", "id": "%1", "host": "lap"}),
            ("home", {"session": "w"}), ("session_id", "abc"), ("configured", True), ("mode", "print"), ("cwd", "/w")]
    for n in range(len(keys) + 1):
        for chosen in itertools.combinations(keys, n):
            for kind in (None, "terminal", "files"):
                st = dict(chosen)
                if kind:
                    st["kind"] = kind
                if kind == "files" and kinds.has_conversation(st):
                    continue
                yield st


class AsItWas(unittest.TestCase):
    def test_listens_and_the_channel_list_answer_as_they_did(self):
        seen = 0
        for st in every_state():
            for name, category in (("work", "專案"), (config.AUDIT_CHANNEL, None), (config.GUIDE_CHANNEL, None), ("old", config.ARCHIVE_CATEGORY)):
                c = {"name": name, "_category": category}
                for state in (st, kinds.stamp(dict(st))):  # as written before this step, and as saved since
                    self.assertEqual(posts.listens(state), listens_as_it_was(st), state)
                    if not (st.get("kind") == "files" and st.get("plain")):  # (a files channel marked plain as well: nothing writes that)
                        self.assertEqual(dcctl.kind_of(c, state), kind_of_as_it_was(c, st), (name, state))
                    seen += 1
        self.assertGreater(seen, 5000)
        for c in ({"name": "work", "_category": None}, {"name": "old", "_category": config.ARCHIVE_CATEGORY}):
            self.assertEqual(dcctl.kind_of(c, None), kind_of_as_it_was(c, None))
            self.assertEqual(dcctl.kind_of(c, {}), kind_of_as_it_was(c, {}))


class Kind(unittest.TestCase):
    def test_archived_first_then_the_kind_then_the_flags(self):
        K = kinds
        for st, kind in (({}, K.UNSET), (None, K.UNSET), ({"cwd": "/w", "configured": True}, K.UNSET), ({"mode": "print"}, K.UNSET),
                         ({"tmux": "dc-x"}, K.CONVERSATION), ({"pane": {"id": "%1"}}, K.CONVERSATION), ({"session_id": "a"}, K.CONVERSATION),
                         ({"home": {"session": "w"}}, K.CONVERSATION), ({"plain": True}, K.RECORD), ({"plain": True, "session_id": "a"}, K.RECORD),
                         ({"kind": "terminal", "home": {"session": "w"}}, K.TERMINAL), ({"kind": "files", "files": {"root": "/w"}}, K.FILES),
                         ({"kind": "terminal", "plain": True}, K.TERMINAL),
                         ({"archived": {"pane": None}, "kind": "terminal"}, K.ARCHIVED), ({"archived": {"pane": None}, "tmux": "x"}, K.ARCHIVED),
                         ({"kind": "board"}, K.UNKNOWN), ({"kind": "board", "tmux": "x"}, K.UNKNOWN), ({"kind": ""}, K.UNKNOWN), ({"kind": 3}, K.UNKNOWN),
                         ({"kind": "conversation"}, K.UNSET), ({"kind": "record"}, K.UNSET),
                         ({"kind": "conversation", "connector": "claude_code", "cwd": "/w", "configured": True}, K.UNSET)):
            self.assertEqual(K.channel_kind(st), kind, st)

    def test_not_set_up_is_not_a_record_and_an_unknown_kind_is_no_error(self):
        self.assertNotEqual(kinds.channel_kind({"cwd": "/w"}), kinds.RECORD)
        self.assertEqual(dcctl.kind_of({"name": "x"}, {"cwd": "/w"}), ("❔", "還沒設定"))
        self.assertEqual(dcctl.kind_of({"name": "x"}, {"kind": "board", "tmux": "dc-x"}), ("❔", "不認得的種類（board）"))
        self.assertFalse(posts.listens({"kind": "board", "tmux": "dc-x"}), "nobody types into what this version does not know")

    def test_while_the_flags_are_there_they_speak_for_the_two_kinds_that_rest_on_them(self):
        """A state stamped "conversation" and then made a plain channel (or the other way), read before it is saved
        again: what the flags say. With no flag saying anything it is not set up, whatever was written: a
        conversation channel after !reset, a plain channel with `plain` taken off (as a version of before this
        step answers for them)."""
        self.assertEqual(kinds.channel_kind({"kind": "conversation", "connector": "claude_code", "plain": True}), kinds.RECORD)
        self.assertEqual(kinds.channel_kind({"kind": "record", "tmux": "dc-x"}), kinds.CONVERSATION)
        for left in ({"kind": "record", "configured": True}, {"kind": "conversation", "connector": "claude_code", "cwd": "/w"}):
            self.assertEqual(kinds.channel_kind(left), kinds.UNSET, left)
            self.assertIsNone(kinds.connector_of(left), left)
            c = {"name": "work", "_category": None}
            self.assertEqual(dcctl.kind_of(c, left), kind_of_as_it_was(c, {k: v for k, v in left.items() if k not in ("kind", "connector")}))

    def test_the_connector_of_a_conversation(self):
        self.assertEqual(kinds.connector_of({"tmux": "dc-x"}), "claude_code")
        self.assertEqual(kinds.connector_of({"tmux": "dc-x", "connector": "another_tool"}), "another_tool")
        for st in ({}, None, {"plain": True}, {"kind": "terminal", "home": {"session": "w"}}, {"kind": "files"}, {"archived": {"pane": None}},
                   {"kind": "board", "connector": "x"}):
            self.assertIsNone(kinds.connector_of(st), st)


class Stamped(unittest.TestCase):
    def test_the_two_fields_as_the_flags_tell_them(self):
        s = kinds.stamp
        self.assertEqual(s({"tmux": "dc-x", "cwd": "/w"}), {"tmux": "dc-x", "cwd": "/w", "kind": "conversation", "connector": "claude_code"})
        self.assertEqual(s({"plain": True, "configured": True}), {"plain": True, "configured": True, "kind": "record"})
        self.assertEqual(s({"cwd": "/w"}), {"cwd": "/w"}, "nothing said: nothing written")
        self.assertEqual(s({"kind": "conversation", "connector": "claude_code", "cwd": "/w"}), {"cwd": "/w"}, "its conversation gone: taken off again")
        self.assertEqual(s({"kind": "conversation", "connector": "claude_code", "plain": True}), {"plain": True, "kind": "record"})
        self.assertEqual(s({"kind": "record", "session_id": "a"}), {"session_id": "a", "kind": "conversation", "connector": "claude_code"})
        self.assertEqual(s({"session_id": "a", "connector": "another_tool"})["connector"], "another_tool", "a connector written is kept")
        for own in ({"kind": "terminal", "home": {"session": "w"}}, {"kind": "files", "files": {"root": "/w"}}, {"kind": "board", "tmux": "x"},
                    {"archived": {"pane": None}, "cwd": "/w"}):
            self.assertEqual(s(dict(own)), own, "left as it is")
        self.assertEqual(s("not a state"), "not a state")

    def test_every_save_stamps_every_channel(self):
        written = {}
        bot = mock.Mock(channels_state={"1": {"tmux": "dc-x"}, "2": {"plain": True}, "3": {"cwd": "/w"}, "4": {"kind": "terminal", "home": {}}})
        with mock.patch.object(main, "save_json", lambda path, data: written.update(data)):
            main.Bot.save(bot)
        self.assertEqual({k: (v.get("kind"), v.get("connector")) for k, v in written.items()},
                         {"1": ("conversation", "claude_code"), "2": ("record", None), "3": (None, None), "4": ("terminal", None)})

    def test_the_state_file_is_written_in_one_place(self):
        """Bot.save(), which stamps. Four places wrote the file themselves until this was found (a print-mode turn's
        end, !reset, !cwd, !mode), and left `kind` and `connector` as an earlier save had put them."""
        folder = os.path.join(ROOT, "bot")
        writes = []
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for fn in ast.walk(tree):
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for node in ast.walk(fn):
                        if isinstance(node, ast.Call) and any(isinstance(a, ast.Name) and a.id == "CHANNELS" for a in node.args):
                            writes.append((name, fn.name, getattr(node.func, "id", getattr(node.func, "attr", "?"))))
        self.assertEqual([w for w in writes if w[2] not in ("load_json", "exists")], [("main.py", "save", "save_json")])  # reads: not writers
        self.assertIn(("main.py", "__init__", "load_json"), writes, "(the walk does see calls that name the file)")

    def test_reset_cwd_mode_and_a_print_turn_leave_the_file_as_the_flags_say(self):
        """What those four did, by the one way: a channel whose only conversation key is session_id, stamped by an
        earlier save, loses the key; the next thing written is without `kind` and `connector`."""
        written = {}
        st = {"session_id": "abc", "cwd": "/w", "mode": "print", "configured": True}
        bot = mock.Mock(channels_state={"1": st})
        with mock.patch.object(main, "save_json", lambda path, data: written.update(json.loads(json.dumps(data)))):
            main.Bot.save(bot)
            self.assertEqual((written["1"]["kind"], written["1"]["connector"]), ("conversation", "claude_code"))
            st.pop("session_id")  # !reset, !cwd
            main.Bot.save(bot)
            self.assertEqual((written["1"].get("kind"), written["1"].get("connector")), (None, None))
            st["session_id"] = "new"  # the end of a print-mode turn
            main.Bot.save(bot)
            self.assertEqual((written["1"]["kind"], written["1"]["connector"]), ("conversation", "claude_code"))

    def test_a_version_of_before_this_step_reads_a_stamped_state_as_before(self):
        """A rollback puts the code back, not the state: the readers of before compare `kind` with "terminal" and
        "files" only, so the two new values are, to them, as no kind at all."""
        for st in every_state():
            self.assertEqual(listens_as_it_was(kinds.stamp(dict(st))), listens_as_it_was(st), st)
            c = {"name": "work", "_category": None}
            self.assertEqual(kind_of_as_it_was(c, kinds.stamp(dict(st))), kind_of_as_it_was(c, st), st)


class Archived(unittest.TestCase):
    def test_why_a_channel_is_archived(self):
        st = {"pane": {"id": "%1"}, "pane_session": "w", "session_id": "a", "cwd": "/w"}
        self.assertEqual(kinds.archived_why({"archived": archive.record(st, kinds.DETACHED)}), "detached")
        self.assertEqual(kinds.archived_why({"archived": archive.record(st, kinds.MOVED)}), "channel_moved")
        self.assertEqual(archive.record(st), {"pane": {"id": "%1"}, "pane_session": "w", "home": None, "session_id": "a", "cwd": "/w"},
                         "no reason given: the record as it always was")
        self.assertIsNone(kinds.archived_why({"archived": archive.record(st)}), "archived before this was written down")
        self.assertIsNone(kinds.archived_why({"tmux": "dc-x"}))
        self.assertIsNone(kinds.archived_why(None))
        self.assertEqual(kinds.archived_why({"archived": {"why": "closed"}}), "closed", "open: another reason is read back as written")


class ForTheTool(unittest.TestCase):
    def test_standard_library_only(self):
        """tools/dcctl reads it under the system Python."""
        with open(os.path.join(ROOT, "bot", "kinds.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read(), feature_version=(3, 8))
        self.assertEqual([n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))], [])


if __name__ == "__main__":
    unittest.main()
