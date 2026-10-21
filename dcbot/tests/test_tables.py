"""C-71a: Markdown tables in Claude's final reply, made readable on Discord: a small ASCII table becomes an aligned
code block, a Chinese (or too wide) one a list; big ones, and anything inside ``` blocks, stay as they are."""
import asyncio
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from bot import ask, config, messages, tables, tmux_bridge
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)
from tests.test_turn_end import Ch, Host


def table(*rows, delimiter=None):
    head = rows[0]
    line = lambda r: "| " + " | ".join(r) + " |"  # noqa: E731
    return "\n".join([line(head), delimiter or "|" + "|".join("---" for _ in head) + "|"] + [line(r) for r in rows[1:]])


class Rewrite(unittest.TestCase):
    def test_a_small_ascii_table_is_an_aligned_code_block(self):
        text = "Results:\n\n" + table(("model", "acc", "time"), ("**resnet**", "0.91", "12s"), ("vit", "0.93", "`30s`"),
                                      delimiter="|---|---:|:---:|") + "\n\nDone."
        got, n = tables.rewrite(text)
        self.assertEqual(n, 1)
        self.assertEqual(got, "\n".join(["Results:", "", "```",
                                         "model    acc  time",
                                         "------  ----  ----",
                                         "resnet  0.91  12s",
                                         "vit     0.93  30s",
                                         "```", "", "Done."]), "bold and code marks around a cell dropped; colons align")

    def test_chinese_is_a_list(self):
        got, n = tables.rewrite(table(("方法", "準確率", "備註"), ("基準", "0.91", "原本的"), ("新的", "0.93", "")))
        self.assertEqual((got, n), ("- **基準**：準確率 0.91；備註 原本的\n- **新的**：準確率 0.93", 1), "an empty cell is left out")

    def test_a_row_whose_first_cell_is_empty(self):
        got, _ = tables.rewrite(table(("項目", "值", "說明"), ("", "3", "第二列")))
        self.assertEqual(got, "- 值 3；說明 第二列")

    def test_full_width_and_emoji_are_not_ascii(self):
        for cell in ("ＡＢ", "ok ✅"):
            got, _ = tables.rewrite(table(("a", "b"), ("1", cell)))
            self.assertTrue(got.startswith("- **1**："), cell)

    def test_an_ascii_table_too_wide_for_a_phone_is_a_list(self):
        wide = "x" * tables.WIDE
        got, n = tables.rewrite(table(("name", "path"), ("one", wide), ("two", wide + "y" * 10)))
        self.assertEqual(n, 1)
        self.assertEqual(got.splitlines(), ["- **one**：path " + wide, "- **two**：path " + wide + "y" * 10])
        got, _ = tables.rewrite(table(("name", "path"), ("one", "x" * (tables.WIDE - 6))))
        self.assertTrue(got.startswith("```"), "WIDE itself (4 + 2 + 54): a block")

    def test_too_many_columns_or_rows_are_left_as_they_are(self):
        many = table(tuple("abcdef"), tuple("123456"))
        self.assertEqual(tables.rewrite(many), (many, 0))
        long = table(("a", "b"), *[(str(i), str(i)) for i in range(tables.ROWS + 1)])
        self.assertEqual(tables.rewrite(long), (long, 0))
        five = table(tuple("abcde"), *[tuple("12345")] * tables.ROWS)
        self.assertEqual(tables.rewrite(five)[1], 1, "five columns, ROWS rows: the limits themselves are fine")

    def test_inside_a_code_block_nothing_is_touched(self):
        text = "Markdown:\n```md\n" + table(("a", "b"), ("1", "2")) + "\n```\n" + table(("a", "b"), ("1", "2"))
        got, n = tables.rewrite(text)
        self.assertEqual(n, 1)
        self.assertTrue(got.startswith("Markdown:\n```md\n| a | b |\n|---|---|\n| 1 | 2 |\n```\n```\na  b"))

    def test_not_tables(self):
        for text in ("a | b\nnot a delimiter\n1 | 2", "| a | b |\n|---|---|", "only | one\n|---|\n| x |",
                     "price $5 | tax\n-- | --\n1 | 2", "| a | b |\n|---|---|---|\n| 1 | 2 |"):
            self.assertEqual(tables.rewrite(text), (text, 0), text)

    def test_an_escaped_bar_is_a_character(self):
        got, _ = tables.rewrite(table(("op", "means"), ("a \\| b", "or")))
        self.assertIn("a | b  or", got)

    def test_rows_end_at_a_line_without_a_bar_and_a_short_row_gets_empty_cells(self):
        text = "| a | b |\n|---|---|\n| 1 |\n| 2 | 3 |\nafter"
        got, _ = tables.rewrite(text)
        self.assertEqual(got, "```\na  b\n-  -\n1\n2  3\n```\nafter")

    def test_a_bar_inside_a_code_span_is_part_of_its_cell(self):
        """#code-review on 3db7694: `ps aux | grep x` was cut in two, 找程序 lost and grep x put under 用途."""
        got, _ = tables.rewrite("| 指令 | 用途 |\n|---|---|\n| `ps aux | grep x` | 找程序 |\n| `ls` | 列檔 |")
        self.assertEqual(got, "- **ps aux | grep x**：用途 找程序\n- **ls**：用途 列檔")
        got, _ = tables.rewrite("| a | b |\n|---|---|\n| `a | b` | pipe |\n| ``x | `y` | z`` | two |")
        self.assertEqual([row.split("  ")[0].strip() for row in got.splitlines()[3:5]], ["a | b", "x | `y` | z"])
        self.assertEqual([row.split()[-1] for row in got.splitlines()[3:5]], ["pipe", "two"])

    def test_a_row_with_more_cells_than_the_header_leaves_the_table_as_it_is(self):
        for text in ("| a | b |\n|---|---|\n| 1 | 2 | 3 |", "| 甲 | 乙 |\n|---|---|\n| 1 | 2 |\n| 3 | `4` | 5 |",
                     "| a | b |\n|---|---|\n| 1 | `unclosed | x |"):
            self.assertEqual(tables.rewrite(text), (text, 0), text)

    def test_a_first_cell_bold_in_part_is_not_bolded_again(self):
        got, _ = tables.rewrite("| 項目 | 說明 |\n|---|---|\n| **a** b | 中 |")
        self.assertEqual(got, "- **a** b：說明 中")

    def test_tilde_blocks_and_fences_in_cells(self):
        text = "~~~\n| a | b |\n|---|---|\n| 1 | 2 |\n~~~\n```\nx\n~~~\n| a | b |\n|---|---|\n| 1 | 2 |\n```"
        self.assertEqual(tables.rewrite(text), (text, 0), "~~~ is a code block too, and ~~~ does not close ```")
        text = "| a | b |\n|---|---|\n| ``` | 2 |"
        self.assertEqual(tables.rewrite(text), (text, 0), "a fence in a cell would end the block early")

    def test_two_tables_and_a_failure_gives_the_text_back(self):
        text = table(("a", "b"), ("1", "2")) + "\n\n" + table(("甲", "乙"), ("1", "2"))
        self.assertEqual(tables.rewrite(text)[1], 2)
        with mock.patch.object(tables, "as_block", side_effect=ValueError("boom")):
            self.assertEqual(tables.rewrite(text), (text, 0))
        self.assertEqual(tables.rewrite(None), ("", 0))


class Setting(unittest.TestCase):
    def test_true_unless_it_says_false(self):
        self.assertTrue(messages.small_tables_of(None))
        self.assertTrue(messages.small_tables_of(True))
        self.assertFalse(messages.small_tables_of(False))
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertTrue(messages.small_tables_of("no"))
        self.assertIn("dcbot.small_tables is 'no'", out.getvalue())

    def test_read_with_the_rest_into_memory(self):
        saved = dict(messages.loaded)
        try:
            with tempfile.TemporaryDirectory() as inst:
                with open(os.path.join(inst, "plugins.json"), "w") as f:
                    f.write('{"dcbot": {"small_tables": false}}')
                with mock.patch.dict(os.environ, {"ALT_INSTANCE": inst}):
                    messages.loaded["stamp"] = None
                    messages.load()
                self.assertFalse(messages.loaded["small_tables"])
        finally:
            messages.loaded.clear()
            messages.loaded.update(saved)


class OnTheReply(unittest.TestCase):
    """Only the final reply (Stop) is rewritten, and only while the setting says so."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (ask.ASKS, ask.ANSWERS, config.OWNER_ID, config.INTERIM, dict(messages.loaded))
        ask.ASKS, ask.ANSWERS = os.path.join(self.tmp.name, "asks"), os.path.join(self.tmp.name, "answers")
        config.OWNER_ID, config.INTERIM = 42, 0
        self.ch = Ch()
        self.bridge = tmux_bridge.TmuxBridge(Host(self.ch))

    def tearDown(self):
        ask.ASKS, ask.ANSWERS, config.OWNER_ID, config.INTERIM, loaded = self.saved
        messages.loaded.clear()
        messages.loaded.update(loaded)
        self.tmp.cleanup()

    def reply(self, text):
        async def go():
            ev = {"channel": "5", "session_id": "abc"}
            await self.bridge.handle(dict(ev, hook_event_name="UserPromptSubmit", prompt="go"))
            await self.bridge.handle(dict(ev, hook_event_name="Stop", last_assistant_message=text))
        asyncio.run(go())
        return self.ch.sent[-1].content

    def test_on_by_default_off_when_said(self):
        text = table(("方法", "準確率"), ("基準", "0.91"))
        self.assertTrue(self.reply(text).startswith("- **基準**：準確率 0.91"))
        messages.loaded["small_tables"] = False
        self.assertTrue(self.reply(text).startswith("| 方法 | 準確率 |"))


if __name__ == "__main__":
    unittest.main()
