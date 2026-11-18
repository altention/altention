"""C-148 batch 3: golden files of what the bot leaves on Discord and reads back later, or what an older button
still carries (spec §15.3, §15.5, §15.6): the slash commands (names and options: the words are not locked), every
custom_id's form, the topics and names it writes and later matches, and the line under a reply. Several of these
are Chinese now (the archive's 「📦 原分類」, the attached pane's topic): v0.7.0 makes them language-free (C-147
Q08-04) and changes these files on purpose. A change here is a change of interface: tools/golden update <case>, and a
"golden:" line in the commit."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py; 11-08: one left a real dcremote-owner)
import ast
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # the engine
from altention import testing  # noqa: E402
from bot import archive, ask, events, routes, slash  # noqa: E402
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

HERE = os.path.dirname(os.path.abspath(__file__))
BOT = os.path.join(os.path.dirname(HERE), "bot")
V070 = "（v0.7.0 改成跟語言無關：C-147 Q08-04）"


def tree(name):
    with open(os.path.join(BOT, name), encoding="utf-8") as f:
        return ast.parse(f.read())


def text_of(node, names):
    """A string expression as its form: literals as they are, names of module constants by value, anything else
    as {…}, % and + kept as written."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and node.id in names:
        return names[node.id]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        left = text_of(node.left, names)
        parts = node.right.elts if isinstance(node.right, ast.Tuple) else [node.right]
        for p in parts:
            value = text_of(p, names) if isinstance(p, (ast.Constant, ast.Name)) and text_of(p, names) != "{…}" else None
            i = min((left.find(s) for s in ("%s", "%d") if left.find(s) >= 0), default=-1)
            if i >= 0 and value is not None:
                left = left[:i] + value + left[i + 2:]
            elif i >= 0:
                left = left[:i] + "{" + left[i + 1] + "}" + left[i + 2:]
        return left
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return text_of(node.left, names) + text_of(node.right, names)
    return "{…}"


class Slash(unittest.TestCase):
    def test_the_commands_and_their_options(self):
        """§15.3: each slash command's name, and its options (name, type, required or not)."""
        rows = []
        for fn in ast.walk(tree("slash.py")):
            if not isinstance(fn, ast.AsyncFunctionDef):
                continue
            names = [kw.value.value for d in fn.decorator_list if isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "command"
                     for kw in d.keywords if kw.arg == "name" and isinstance(kw.value, ast.Constant)]
            if not names:
                continue
            args = fn.args.args[1:]
            defaults = [None] * (len(args) - len(fn.args.defaults)) + list(fn.args.defaults)
            opts = ["%s: %s%s" % (a.arg, ast.unparse(a.annotation) if a.annotation else "?", "" if d is None else " = " + ast.unparse(d))
                    for a, d in zip(args, defaults)]
            rows.append("/%s(%s)\n" % (names[0], ", ".join(opts)))
        rows += ["/%s() → %s\n" % (n, v[1]) for n, v in list(slash.PLAIN.items()) + list(slash.CLAUDE.items())]  # made in a loop
        testing.golden("discord/slash-commands", "".join(sorted(rows)), "§15.3", folder=HERE)


class CustomIds(unittest.TestCase):
    def test_every_custom_id_form(self):
        """§15.5: the form of every custom_id the bot puts on a button, a select or a modal ({d}, {s}: a value)."""
        forms = set()
        for name in sorted(os.listdir(BOT)):
            if not name.endswith(".py"):
                continue
            t = tree(name)
            consts = {n.targets[0].id: n.value.value for n in t.body if isinstance(n, ast.Assign) and len(n.targets) == 1
                      and isinstance(n.targets[0], ast.Name) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)}
            for call in (n for n in ast.walk(t) if isinstance(n, ast.Call)):
                for kw in call.keywords:
                    if kw.arg == "custom_id" and not isinstance(kw.value, ast.Call):
                        forms.add(text_of(kw.value, consts))
        forms.discard("value")  # the modal's text field, not a button
        forms.discard("{…}{…}")  # /attach's: its stamp is built first (below, as routes builds it)
        forms.add(routes.BUTTON + "{d}:{pane key, or %s<name>}" % routes.PROJECT)
        forms |= {ask.custom_id("7-1", 0, w) for w in ("1", "s", "o", "y")}
        testing.golden("discord/custom-ids", "".join(f + "\n" for f in sorted(forms)), "§15.5", folder=HERE)


class Topics(unittest.TestCase):
    def test_topics_and_names_matched_later(self):
        """§15.6: what is written and matched again later (an archived channel found again by its topic)."""
        pane = {"session": "work", "window": "2", "cwd": os.path.join(os.path.expanduser("~"), "proj")}
        with mock.patch.object(archive.time, "localtime", return_value=archive.time.gmtime(1793000000)):
            archived = archive.topic_with(routes.topic("lap", pane), {"from": {"name": "專案"}, "at": 1793000000})
            no_category = archive.topic_with("", {"from": None, "at": 1793000000})
            unknown = archive.topic_with("x", {"at": 1793000000})
        posts = tree("posts.py")
        made = sorted(set(kw.value.value for c in ast.walk(posts) if isinstance(c, ast.Call)
                          and getattr(c.func, "attr", "") == "create_text_channel"
                          for kw in c.keywords if kw.arg == "topic" and isinstance(kw.value, ast.Constant)))
        got = {"attached (this machine)": routes.topic("lap", pane),
               "attached (another machine)": routes.topic("box", dict(pane, cwd="/w/them/proj")),
               "archived": archived, "archived, no category": no_category, "archived, category unknown": unknown,
               "archived, read back": [archive.topic_without(archived), archive.noted(archived)[0]],
               "made by dc post": made}
        testing.golden("discord/topics", got, "§15.6" + V070, folder=HERE)

    def test_a_panes_channel_name(self):
        """§15.6: <session>-<window name or number>, <project>-<n>-… for a number, lowercase, - for the rest, 90."""
        cases = [({"session": "work", "window": "2"}, "proj"), ({"session": "work", "window": "2", "window_name": "My Tests"}, "proj"),
                 ({"session": "3", "window": "0"}, "回測"), ({"session": "a b.c", "window": "1"}, "p"),
                 ({"session": "x" * 100, "window": "1"}, "p"), ({"session": "---", "window": "!"}, "p")]
        rows = "".join("%r → %s\n" % ((p, project), routes.channel_name(p, project)) for p, project in cases)
        testing.golden("discord/channel-names", rows, "§15.6", folder=HERE)


class Footer(unittest.TestCase):
    def test_the_line_under_a_reply(self):
        """§15.6: -# <tool×n, …> · N 秒 · 背景工作 N 個進行中 · context P%."""
        with mock.patch.object(events.transcript, "window", return_value=200000):
            rows = [events.footer({"Bash": 3, "Read": 1}, 12.4, 2, (40000, "m")), events.footer({}, 3, 0, None),
                    events.footer({"Edit": 1}, None, 0, None), events.footer({}, None, 0, None)]
        testing.golden("discord/footer", "".join("%r\n" % r for r in rows), "§15.6" + V070, folder=HERE)


if __name__ == "__main__":
    unittest.main()
