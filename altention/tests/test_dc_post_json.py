"""C-172a P1d: altention dc post --json on the command line (altention/dc/post.py build()): one message's payload from
a file or stdin ("-"), and the flags that go with it; what is refused before anything is queued."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import contextlib
import io
import os
import shutil
import tempfile
import unittest

from altention.dc import post


class Build(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        with open(os.path.join(self.dir, "p.json"), "w") as f:
            f.write('{"content": "hi"}')

    def build(self, *argv, stdin=""):
        read = []

        def stdin_text():
            read.append(1)
            return stdin
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            got = post.build(list(argv), stdin_text, {}, "host-a", self.dir, machine="lap", claude={"DCBOT_CHANNEL": "5"})
        return got, read

    def refused(self, *argv, stdin=""):
        with self.assertRaises(SystemExit) as e:
            self.build(*argv, stdin=stdin)
        return str(e.exception.code)

    def test_a_file_or_stdin(self):
        got, read = self.build("lab", "--json", "p.json")
        self.assertEqual((got["json"], got["wait"], got["text"], read), ({"content": "hi"}, post.JSON_WAIT, "", []))
        got, read = self.build("lab", "--json", "-", "--wait", "5", stdin='{"flags": 32768, "components": []}')
        self.assertEqual((got["json"]["flags"], got["wait"], read), (32768, 5, [1]))

    def test_what_goes_with_it(self):
        got, _ = self.build("lab", "--json", "p.json", "--mention", "owner", "--mention", "user:7", "--silent",
                            "--no-embeds", "--reply-to", "555", "--thread-new", "題目")
        self.assertEqual({k: got[k] for k in ("ping", "silent", "no_embeds", "reply_to", "thread_new")},
                         {"ping": ["owner", "user:7"], "silent": True, "no_embeds": True, "reply_to": "555",
                          "thread_new": "題目"})
        got, _ = self.build("lab", "--json", "p.json", "--edit", "900")
        self.assertEqual(got["edit_message"], "900")

    def test_refused_before_anything_is_queued(self):
        for argv, words in ((("lab", "--json", "p.json", "more words"), "不能再帶文字"),
                            (("lab", "--json", "p.json", "--board", "b", "--mention", "owner"), "不 @ 人"),
                            (("lab", "--json", "p.json", "--history"), "--history"),
                            (("lab", "--json", "nope.json"), "讀不到"),
                            (("lab", "--json", "p.json", "--edit", "x"), "--edit 要給訊息 ID"),
                            (("lab", "--json", "p.json", "--edit", "9", "--reply-to", "8"), "只改內容"),
                            (("lab", "--thread-new", "t", "x"), "只跟 --json 一起用"),
                            (("lab", "--mention", "owner", "x"), "只跟 --json 一起用"),
                            (("lab", "--reply-to", "abc", "x"), "--reply-to 要給訊息 ID"),
                            (("lab", "--reply-to", "5", "--board", "b", "x"), "只用在貼訊息"),
                            (("lab", "--json"), "--json 要給值")):
            self.assertIn(words, self.refused(*argv), argv)
        self.assertIn("不是 JSON", self.refused("lab", "--json", "-", stdin="{oops"))
        self.assertIn("JSON 物件", self.refused("lab", "--json", "-", stdin="[1]"))
        self.assertIn("JSON 物件", self.refused("lab", "--json", "-", stdin="{}"))
        self.assertIn("1 到 10", self.refused("lab", "--board", "b", "--json", "-", stdin="[]"))
        self.assertIn("1 到 10", self.refused("lab", "--board", "b", "--json", "-", stdin='[{"content": "a"}, {}]'))

    def test_a_framed_board_a_payload_or_a_list(self):
        got, _ = self.build("lab", "--board", "本週", "--json", "-", stdin='[{"content": "a"}, {"content": "b"}]')
        self.assertEqual((got["board"], len(got["json"]), got["wait"]), ("本週", 2, post.JSON_WAIT))
        got, _ = self.build("lab", "--board", "本週", "--json", "p.json", "--wait", "5")
        self.assertEqual((got["json"], got["wait"]), ({"content": "hi"}, 5))

    def test_delete_one_or_several(self):
        got, read = self.build("lab", "--delete", "900", "--why", "寫錯了")
        self.assertEqual((got["delete_messages"], got["why"], got["wait"], read), (["900"], "寫錯了", 36, []))
        self.assertNotIn("delete_message", got)
        got, _ = self.build("lab", "--delete", "900", "901", "900", "902", "--why", "清掉")
        self.assertEqual((got["delete_messages"], got["wait"]), (["900", "901", "902"], 48), "one given twice: once")
        got, _ = self.build("lab", "--delete", "900", "--why", "w", "--wait", "9")
        self.assertEqual(got["wait"], 9)
        many = [str(1000 + i) for i in range(post.DELETE_MOST)]
        got, _ = self.build("lab", "--delete", *many, "--why", "w")
        self.assertEqual((len(got["delete_messages"]), got["wait"]), (20, 150))
        self.assertEqual(got["answer_wait"], 150, "dcctl takes \"wait\" for itself: the bot reads this one (rig drill 11-20)")
        for argv, words in ((("lab", "--delete", "x", "--why", "w"), "--delete 要給訊息 ID"),
                            (("lab", "--delete"), "--delete 要給訊息 ID"),
                            (("lab", "--delete", "900"), "要帶 --why"),
                            (("lab", "--delete", "900", "--why", "w", "還有字"), "單獨用"),
                            (("lab", "--delete", "900", "901", "x", "--why", "w"), "單獨用"),
                            (("lab", "--delete", "900", "--unpause", "--why", "w"), "不能一起用"),
                            (("lab", "--delete", "900", "--rename", "x", "--why", "w"), "單獨用"),
                            (("lab", "--delete", "900", "--drop-held", "--why", "w"), "不能一起用"),
                            (("lab", "--delete", "900", "--pause-until", "1h", "--why", "w"), "不能一起用"),
                            (("lab", "--delete", *many, "1999", "--why", "w"), "一次最多 20 則，請分批"),
                            (("lab", "--delete", "900", "901", "--reset", "--why", "w"), "不能跟"),
                            (("lab", "--json", "p.json", "--delete", "900"), "不能再帶")):
            self.assertIn(words, self.refused(*argv), argv)

    def test_move_its_kinds_and_why(self):
        """C-201 2c1: --move session <name> | tmux bot | tmux user <session>, with --why, alone."""
        got, _ = self.build("lab", "--move", "session", "work", "--why", "整理")
        self.assertEqual((got["move_place"], got["move_target"], got["why"]), ("session", "work", "整理"))
        self.assertNotIn("move_session", got)
        self.assertNotIn("move_to", got, "not the category move (--move-to)")
        got, _ = self.build("lab", "--move", "tmux", "user", "main", "--why", "w")
        self.assertEqual((got["move_place"], got["move_target"], got["move_session"]), ("tmux", "user", "main"))
        got, _ = self.build("lab", "--move", "tmux", "bot", "--why", "w")
        self.assertEqual((got["move_place"], got["move_target"]), ("tmux", "bot"))
        for argv, words in ((("lab", "--move", "session", "work"), "要帶 --why"),
                            (("lab", "--move", "jump", "x", "--why", "w"), "--move 的寫法"),
                            (("lab", "--move", "tmux", "there", "--why", "w"), "--move 的寫法"),
                            (("lab", "--move", "tmux", "user", "--why", "w"), "--move 的寫法"),
                            (("lab", "--move", "session", "--why", "w"), "--move 的寫法"),
                            (("lab", "--move", "session", "x", "--why", "w", "還有字"), "單獨用"),
                            (("lab", "--move", "session", "x", "--rename", "y", "--why", "w"), "單獨用"),
                            (("lab", "--json", "p.json", "--move", "session", "x"), "不能再帶")):
            self.assertIn(words, self.refused(*argv), argv)

    def test_a_plain_post_may_answer_a_message_and_go_silently(self):
        got, _ = self.build("lab", "--reply-to", "555", "--silent", "報告")
        self.assertEqual((got["text"], got["reply_to"], got["silent"]), ("報告", "555", True))
        self.assertNotIn("json", got)
        self.assertNotIn("wait", got, "not waited for unless asked, as before")



class Reset(Build):
    """v0.6.1 item 4: --reset --why "…" [--then "…"] (the bot's side: dcbot/tests/test_resets.py)."""
    def test_what_goes_to_the_bot(self):
        got, read = self.build("lab", "--reset", "--why", "context 滿了")
        self.assertEqual((got["reset"], got["why"], got["wait"], got["text"], "then" in got, read),
                         (True, "context 滿了", 30, "", False, []), "stdin not read")
        got, _ = self.build("lab", "--reset", "--why", "w", "--then", " 接續請讀 docs/h.md ")
        self.assertEqual(got["then"], "接續請讀 docs/h.md")

    def test_what_is_refused(self):
        for argv, said in ((("lab", "--reset"), "--why"),
                           (("lab", "--then", "x", "--why", "w"), "只跟 --reset"),
                           (("lab", "--reset", "--why", "w", "--then", " "), "--then 要給"),
                           (("lab", "--reset", "--why", "w", "hello"), "單獨用"),
                           (("lab", "--reset", "--why", "w", "--unpause"), "不能跟"),
                           (("lab", "--reset", "--why", "w", "--json", "p.json"), "--json")):
            self.assertIn(said, self.refused(*argv), argv)

if __name__ == "__main__":
    unittest.main()
