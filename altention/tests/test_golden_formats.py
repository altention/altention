"""C-148 batch 2: golden files of the formats another program, version or machine reads (spec §13.4, §13.5): the
post `altention dc post` hands the bot for each kind of post, an update card, and the words source.json's check
gives (spec writes them as originals). Forms (keys, types, fixed values), never times or ids. A change here is a
change of interface: tools/golden update <case>, and a "golden:" line in the commit."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import contextlib
import io
import os
import tempfile
import unittest

from altention import sources, testing
from altention.dc import card, post

KEEP = ("channel", "notify", "create", "board", "activity", "secret", "quiet", "history", "move_to", "on", "wait",
        "from_channel", "args", "kind", "lang", "tool", "json", "edit_message", "reply_to", "thread_new", "silent",
        "no_embeds", "ping")  # values that say what kind of post it is


class Posts(unittest.TestCase):
    def build(self, argv, stdin=""):
        out = io.StringIO()
        with contextlib.redirect_stderr(out):
            return post.build(argv, stdin, {"DCBOT_CHANNEL": "55"}, "host-a", "/w/proj", machine="host-a",
                              claude={"DCBOT_CHANNEL": "55"})

    def test_each_kind_of_post(self):
        """§13.4: what each command line becomes (the bot, maybe of another version, reads it)."""
        folder = tempfile.mkdtemp()
        report = os.path.join(folder, "r.png")
        with open(report, "wb") as f:
            f.write(b"\x89PNG\r\n")
        with open(report + ".source.json", "w") as f:
            f.write('{"kind": "chart", "lang": "python", "tool": "matplotlib", "source": "plot()"}')
        payload = os.path.join(folder, "p.json")
        with open(payload, "w") as f:
            f.write('{"flags": 32768, "components": [{"type": 10, "content": "x"}]}')
        cases = [["reports", "hello"], ["@notify", "--wait", "30", "x"], ["@audit", "x"], ["行程", "--board", "本週", "x"],
                 ["新頻道", "--create", "x"], ["st-0", "--activity", "demo/files", "--arg", "path=/r.pdf", "報告"],
                 ["st-0", "--secret", "demo/api-token"], ["audit", "--move-to", "系統", "why"],
                 ["st-0", "--resume", "eb106548", "--quiet"], ["st-0", "--history"],
                 ["專案/demo", "--new-claude", "/w/demo", "--on", "host-b", "主題"], ["reports", "--file", report, "週報"],
                 ["lab", "--json", payload, "--mention", "owner", "--silent", "--no-embeds", "--reply-to", "555"],
                 ["lab", "--json", payload, "--edit", "900"], ["lab", "--json", payload, "--thread-new", "討論"],
                 ["lab", "--reply-to", "555", "--silent", "x"], ["行程", "--board", "本週", "--json", payload]]
        got = {}
        for argv in cases:
            p = self.build(argv)
            got[" ".join({report: "<file>", payload: "<payload>"}.get(a, a) for a in argv)] = testing.shape(p, keep=KEEP)
        testing.golden("formats/post-json", got, "§13.4")

    def test_a_card(self):
        """§13.4: an update card; text past its limit cut with the line it ends with."""
        c = card.build("update-v0.6.0", "新版 v0.6.0" + "長" * 3000,
                       [{"id": "go", "label": "更新", "style": "primary", "action": "update_go", "arg": "v0.6.0"},
                        {"id": "later", "label": "之後再說", "action": "later"}], details="x", channel="update", host="host-a")
        text = c["card"]["text"]
        got = {"shape": testing.shape(c, keep=("id", "style", "close", "action", "arg", "channel")), "ends": text[-20:],
               "length": len(text)}
        testing.golden("formats/card", got, "§13.4")


class Sources(unittest.TestCase):
    def test_the_words_of_a_bad_source(self):
        """§13.5: the originals the check gives for each fault."""
        good = {"kind": "chart", "lang": "python", "tool": "matplotlib", "source": "plot()"}
        cases = [("a list", []), ("kind a number", dict(good, kind=3)), ("kind too long", dict(good, kind="k" * 41)),
                 ("lang odd", dict(good, lang="py thon")), ("source a number", dict(good, source=1)),
                 ("source too big", dict(good, source="x" * (64 * 1024 + 1))), ("good", good)]
        rows = []
        for name, obj in cases:
            got, why = sources.check(obj)
            rows.append("%s → %s\n" % (name, why if why else "ok"))
        testing.golden("formats/source-errors", "".join(rows), "§13.5")


if __name__ == "__main__":
    unittest.main()
