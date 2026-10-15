"""/screen as a picture (bot/screenshot.py) with its text behind 📝 (bot/screens.py)."""
import asyncio
import io
import unittest

from bot import screens, screenshot
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)


class Parse(unittest.TestCase):
    def test_colours_and_attributes_follow_the_escape_sequences(self):
        rows = screenshot.parse("\x1b[1;31mab\x1b[0m c\x1b[38;5;246md\x1b[38;2;1;2;3me\x1b[38:2::4:5:6mf\x1b[7mg")
        cells = {c[0]: c[1] for c in rows[0]}
        self.assertEqual(cells["a"], {"bold": True, "fg": (205, 49, 49)})
        self.assertEqual(cells[" "], {})
        self.assertEqual(cells["d"]["fg"], (148, 148, 148))
        self.assertEqual(cells["e"]["fg"], (1, 2, 3))
        self.assertEqual(cells["f"]["fg"], (4, 5, 6))
        self.assertTrue(cells["g"]["rev"])

    def test_wide_characters_take_two_cells_and_trailing_blank_rows_go(self):
        rows = screenshot.parse("中a\x1b]8;;http://x\x1b\\b\n\n  \n")
        self.assertEqual([(c[0], c[2]) for c in rows[0]], [("中", 2), ("a", 1), ("b", 1)])
        self.assertEqual(len(rows), 1)

    def test_render_makes_a_png_as_wide_as_the_pane(self):
        png = screenshot.render("\x1b[32m● hi\x1b[0m 中文 ⏵⏵ ⎿\n────", 40)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        from PIL import Image
        w, h = Image.open(io.BytesIO(png)).size
        self.assertGreater(w, 40 * 8)


class Ch(object):
    id = 7

    def __init__(self):
        self.sent, self.reacted = [], []

    async def send(self, text, reference=None, mention_author=True, file=None):
        self.sent.append((text, reference is not None))

    def get_partial_message(self, mid):
        ch = self

        class M(object):
            id = mid

            async def add_reaction(self, emoji):
                ch.reacted.append((mid, emoji))
        return M()


class Bot(object):
    def __init__(self, ch):
        self.ch, self.channels_state, self.saved = ch, {}, 0

    def chan(self, ch):
        return self.channels_state.setdefault(str(ch.id), {})

    def save(self):
        self.saved += 1

    def get_channel(self, cid):
        return self.ch


class Flow(unittest.TestCase):
    def test_picture_gets_a_reaction_and_the_reaction_brings_the_text(self):
        from tests import fakepane
        ch, posted = Ch(), []
        bot = Bot(ch)

        async def color_screen(t):
            return "\x1b[1mhello\x1b[0m\n───\n❯ ", 30

        async def screen(t, lines=0):
            return "hello\n❯"

        async def send(text, view=None, file=None):
            posted.append((text, file is not None))

            class M(object):
                id = 99
            return M()
        with fakepane.use(color_screen=color_screen, screen=screen):
            asyncio.run(screens.post(bot, ch, "s", send))
        self.assertEqual(posted, [("-# 按 📝 看文字版", True)])
        self.assertEqual(ch.reacted, [(99, "📝")])

        class Payload(object):
            channel_id, message_id = 7, 99
        asyncio.run(screens.show_text(bot, Payload()))
        self.assertEqual(ch.sent, [("```\nhello\n❯\n```", True)])

    def test_only_the_latest_screens_are_kept(self):
        st = {}
        for i in range(15):
            screens.remember(st, i, "s%d" % i)
        self.assertEqual(sorted(st["screens"], key=int), [str(i) for i in range(5, 15)])


if __name__ == "__main__":
    unittest.main()
