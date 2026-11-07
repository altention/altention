"""C-127: every way the bot posts what a pane shows (a dialog, a panel, /screen, /key, a shell's output, the screen
it adds when something did not go as it should) masks the keys on it first, as B-12 does for Claude's words and
its tools' output. One made-up key stands on the screen; on each of those ways, what is posted must not hold it.

The ways, by where their text goes out:
  through fence() (tmux_bridge): a conversation that did not start, a slash command's screen, "maybe not
    received", /btw not answered in time, !key
  through fenced() (screens): /screen when the picture cannot be made, 📝 on a screen picture
  of their own: a dialog or panel (menus.body, the title and the buttons, what was pressed), the /screen
    picture and the text kept for 📝 (screens.post), a shell's output in a terminal channel and the file of
    all of it (terminal.body, terminal.full_output), the question Claude Code asks before it starts
    (startup_ask)."""
import asyncio
import unittest
from unittest import mock

from bot import masking, menus, messages, screens, startup_ask, terminal, tmux_bridge
from tests import fakepane
from tests.test_turn_end import Ch, Host
from altention.tests.connectors.claude_code import samples

KEY = "sk-ant-api03-" + "Zq" * 20  # made up, and put together here so that no whole key stands in this file
PIECE = "Zq" * 6  # what of it may never be seen, whole or in part
NOTE = messages.note("masked", n=1)[:-len("1 處像金鑰的字被遮掉了")]  # "-# 有 ", whatever the count


def with_key(view, where="touch sample-file"):
    """A real screen with the key on it, where the sample has `where`."""
    assert where in view
    return view.replace(where, "curl -H 'x-api-key: %s' x" % KEY)


class Posted(unittest.TestCase):
    def clean(self, text, what):
        self.assertNotIn(KEY, text, what)
        self.assertNotIn(PIECE, text, what)
        self.assertIn("[REDACTED]", text, what)


class Fence(Posted):
    def test_fence_and_fenced(self):
        view = with_key(samples.screen("permission"))
        self.clean(tmux_bridge.fence(view), "fence")
        self.assertIn("\n" + NOTE, tmux_bridge.fence(view), "the small line saying so")
        self.assertTrue(tmux_bridge.fence(view).endswith("像金鑰的字被遮掉了"))
        self.assertEqual(tmux_bridge.fence("nothing here"), "```\nnothing here\n```", "nothing masked: no line")
        parts = screens.fenced(view)
        self.clean("".join(parts), "fenced")
        self.assertTrue(parts[-1].endswith("像金鑰的字被遮掉了"))
        self.assertTrue(all(len(p) <= 1900 for p in parts))

    def test_a_key_at_the_cut_is_masked_before_it(self):
        """Masked first, then the tail kept: a key across the cut leaves no piece of it."""
        view = "row\n" + KEY + "\n" + "y" * 1490
        for text in (tmux_bridge.fence(view), menus.body("t", view), terminal.body(view, ""), startup_ask.body({"text": view})):
            self.assertNotIn(PIECE, text)
        long = "\n".join(["x" * 60] * 60 + [KEY] + ["z" * 60] * 60)
        parts = screens.fenced(long)
        self.assertGreater(len(parts), 1)
        self.assertNotIn(PIECE, "".join(parts))


LONG = "sk-ant-api03-" + ("Zq9x_-Wv" * 13)[:95]  # as long as a real key (108): a pane wraps it
TAIL = "Zq9x"  # any piece of it


def wrapped(line, width, frame=""):
    """`line` as a pane `width` wide shows it: broken where the width ends, each row after the first starting with
    the dialog's `frame` (Claude Code's boxes draw "│ " down their left side)."""
    inner = width - len(frame)
    return "\n".join(frame + line[i:i + inner] for i in range(0, len(line), inner))


class Wrapped(Posted):
    """#code-review on ab3a424: a key over two rows (or four, in 40 columns) was masked in its first part only."""

    def test_a_key_wrapped_at_120_80_and_40_columns(self):
        line = '  Bash(curl -H "x-api-key: %s" https://example.invalid)' % LONG
        for width in (120, 80, 40):
            view = wrapped(line, width) + "\n\n Do you want to proceed?\n ❯ 1. Yes"
            self.assertGreaterEqual(view.count("\n"), 3 if width > 40 else 5, width)
            for what, text in (("fence", tmux_bridge.fence(view, 4000)), ("fenced", "".join(screens.fenced(view))),
                               ("menus", menus.body("t", view)), ("terminal", terminal.body(view, "")),
                               ("startup", startup_ask.body({"text": view}))):
                self.assertNotIn(TAIL, text, (width, what))
                self.assertIn("Do you want to proceed?", text, (width, what, "what follows the key is left"))

    def test_a_key_wrapped_inside_a_dialogs_frame(self):
        line = 'export ANTHROPIC_API_KEY=%s; run it' % LONG
        for width in (60, 40):
            view = "╭" + "─" * (width - 2) + "╮\n" + "│ " + wrapped(line, width, "│ ")[2:] + "\n╰" + "─" * (width - 2) + "╯"
            self.assertNotIn(TAIL, tmux_bridge.fence(view, 4000), width)
            self.assertIn("; run it", tmux_bridge.fence(view, 4000).replace("\n", "").replace("│ ", ""), width)

    def test_wherever_the_row_breaks_in_it(self):
        """#code-review on 31b7f85: a row ending after "s", "sk", … "sk-ant" was not taken for a key's start, and
        the next row showed the rest of it. Every break from the first character on, for each kind of key: the
        pane as wide as the row up to the break, so it breaks there and wherever the width comes again."""
        gh = "gh" + "p_" + "Ab3" * 12
        slack = "xox" + "b-" + "12345-abcdef" * 2
        google = "AI" + "za" + "SyD" + "Q" * 32
        aws = "AK" + "IA" + "ABCDEFGHIJ012345"
        for key in (LONG, gh, slack, google, aws):
            line = "  Bash(curl -H x-api-key: %s https://example.invalid)" % key
            at = line.index(key)
            for cut in range(1, len(key)):
                for frame in ("", "│ "):
                    view = wrapped(line, at + cut) if not frame else \
                        "\n".join(frame + line[i:i + at + cut] for i in range(0, len(line), at + cut))
                    out, said = masking.screen(view)
                    flat = out.replace("\n", "").replace("│ ", "")
                    pieces = [key[j:j + 6] for j in range(len(key) - 5)]
                    self.assertFalse([p for p in pieces if p in flat], (key[:4], cut, frame, out))
                    self.assertTrue(said, (key[:4], cut, frame))
                    self.assertIn("example.invalid", flat, (key[:4], cut, frame, "what follows is left (a word right after the "
                                                                     "key, on the next row, may go with it)"))

    def test_rows_that_only_end_and_start_with_words_are_left(self):
        for view in ("hello\nworld", "run sk\nlearn more", "Bash command\n│ ls -la", "a" * 120 + "\n" + "b" * 40):
            self.assertEqual(masking.screen(view), (view, ""), view)

    def test_an_empty_row_ends_it(self):
        out = tmux_bridge.fence("key %s\n\nDo you want to proceed?" % LONG)
        self.assertIn("Do you want to proceed?", out)
        self.assertNotIn(TAIL, out)


class Bridge(Posted):
    """The ways that go through fence(), as they run."""

    def test_btw_not_answered_in_time(self):
        from tests.test_screen_words import Ask
        t = Ask()
        waiting = with_key(samples.screen("btw-answering"), "/btw what's 2+2?") if "/btw what's 2+2?" in samples.screen("btw-answering") \
            else samples.screen("btw-answering").replace("Answering…", "Answering… " + KEY)
        captured = {}

        async def screen(name, lines=0):
            return waiting
        with fakepane.replace("screen", screen):
            said, _ = t.ask([waiting])
        captured["said"] = said
        self.assertIn("還沒有答案", said)
        self.clean(said, "/btw not answered")

    def test_key_and_a_slash_commands_screen(self):
        ch = Ch()
        bridge = tmux_bridge.TmuxBridge(Host(ch))
        bridge.host.chan(ch)["tmux"] = "s"
        view = with_key(samples.screen("permission"))

        async def screen(t, lines=0):
            return view
        with fakepane.use(screen=screen, keys=mock.AsyncMock(), alive=mock.AsyncMock(return_value=True)), \
                mock.patch.object(asyncio, "sleep", mock.AsyncMock()):
            shown = asyncio.run(bridge.key(ch, ["Down"]))
        self.clean(tmux_bridge.fence(shown), "!key (main.py posts fence(shown))")

    def test_a_conversation_that_did_not_start(self):
        e = tmux_bridge.NotOpened(tmux_bridge.NotOpened.NOT_STARTED, "Claude 沒有啟動成功。最後的畫面：\n" +
                                  tmux_bridge.fence(with_key(samples.screen("permission"))))
        self.clean(str(e), "not started")


class Menus(Posted):
    def setUp(self):
        self.ch = Ch()
        self.bridge = tmux_bridge.TmuxBridge(Host(self.ch))
        self.bridge.host.chan(self.ch)["tmux"] = "s"
        self.sent, self.edits = [], []
        test = self

        class Message(object):
            id = 5

            async def edit(self, content=None, view=None):
                test.edits.append((content, view))

        async def send(content, view=None, **kw):
            test.sent.append((content, view))
            return Message()
        self.ch.send = send
        self.ch.get_partial_message = lambda mid: Message()

    def labels(self, view):
        return " ".join(getattr(b, "label", "") or "" for b in (view.children if view else []))

    def test_a_permission_dialog_its_title_and_buttons(self):
        view = with_key(samples.screen("permission"))
        view = view.replace("Yes, and don't ask again", "Yes, and don't ask again for %s" % KEY, 1) \
            if "Yes, and don't ask again" in view else view

        async def raw_screen(t):
            return view
        with fakepane.use(raw_screen=raw_screen, keys=mock.AsyncMock(), alive=mock.AsyncMock(return_value=True)), \
                mock.patch.object(asyncio, "sleep", mock.AsyncMock()):
            asyncio.run(self.bridge.menus.post(self.ch, self.bridge.host.chan(self.ch),
                                               {"message": "Claude needs your permission to run %s" % KEY}))
        content, v = self.sent[-1]
        self.clean(content, "the dialog")
        self.assertIn(NOTE, content)
        self.assertNotIn(PIECE, self.labels(v), "the buttons")

    def test_a_panel(self):
        async def raw_screen(t):
            return samples.screen("model-panel").replace("Select model", "Select model " + KEY, 1)
        sent = []

        async def send(text, view=None):
            sent.append((text, view))
        with fakepane.use(raw_screen=raw_screen):
            shown = asyncio.run(self.bridge.menus.show_panel(self.ch, "s", send, "/model"))
        self.assertTrue(shown)
        self.clean(sent[-1][0], "a panel")
        self.assertNotIn(PIECE, self.labels(sent[-1][1]))

    def test_the_words_of_a_menu(self):
        self.clean(menus.cut("title " + KEY, 200), "a title")
        self.clean(menus.body("Bash " + KEY, "screen"), "the title in the body")
        v = menus.Menus(None).view("7", [(1, "Yes for " + KEY), (2, "No")])
        self.assertNotIn(PIECE, self.labels(v))
        v = menus.Menus(None).panel_view("7", [{"label": "go " + KEY, "keys": ["Enter"]}])
        self.assertNotIn(PIECE, self.labels(v))


class Screen(Posted):
    def test_the_picture_and_the_text_kept_for_the_note(self):
        view = with_key(samples.screen("permission"))
        colored = with_key(samples.screen("permission", esc=True)) if "touch sample-file" in samples.screen("permission", esc=True) \
            else view
        rendered, sent = [], []

        async def color_screen(t):
            return colored, 120

        async def screen(t, lines=0):
            return view

        def render(text, cols):
            rendered.append(text)
            return b"png"

        class Msg(object):
            id = 9

        async def send(text, file=None):
            sent.append(text)
            return Msg()
        ch = Ch()
        ch.get_partial_message = lambda mid: mock.Mock(add_reaction=mock.AsyncMock())
        bot = mock.Mock(chan=lambda c: st, save=lambda: None)
        st = {}
        from bot import screenshot
        with fakepane.use(color_screen=color_screen, screen=screen), mock.patch.object(screenshot, "render", render):
            asyncio.run(screens.post(bot, ch, "s", send))
        self.assertNotIn(PIECE, rendered[0], "the picture")
        self.assertIn(NOTE, sent[0], "said under the picture")
        kept = next(iter(st["screens"].values()))
        self.clean(kept, "the text kept for 📝")

    def test_a_key_the_colours_cut_in_two(self):
        """In the coloured text an escape sequence stands in the middle of the key: the pattern does not see it
        there. The plain text shows it: the picture is drawn from the plain text, masked."""
        half = len(KEY) // 2
        colored = "row\n" + KEY[:half] + "\x1b[1m" + KEY[half:] + "\x1b[0m\n"
        rendered = []

        async def color_screen(t):
            return colored, 120

        async def screen(t, lines=0):
            return "row\n" + KEY + "\n"

        def render(text, cols):
            rendered.append(text)
            return b"png"

        async def send(text, file=None):
            return None
        from bot import screenshot
        with fakepane.use(color_screen=color_screen, screen=screen), mock.patch.object(screenshot, "render", render):
            asyncio.run(screens.post(mock.Mock(), Ch(), "s", send))
        self.assertNotIn(PIECE, rendered[0])

    def test_the_picture_cannot_be_made(self):
        sent = []

        async def color_screen(t):
            return "x", 120

        async def screen(t, lines=0):
            return "row " + KEY

        async def send(text, file=None):
            sent.append(text)
        from bot import screenshot
        with fakepane.use(color_screen=color_screen, screen=screen), \
                mock.patch.object(screenshot, "render", mock.Mock(side_effect=OSError("no font"))), \
                mock.patch("traceback.print_exc"):
            asyncio.run(screens.post(mock.Mock(), Ch(), "s", send))
        self.clean("".join(sent), "/screen as text")


class Terminal(Posted):
    def test_a_shells_output_and_the_file_of_all_of_it(self):
        out = "$ env\nHOME=/x\nANTHROPIC_API_KEY=%s\n" % KEY + "line\n" * 900
        self.clean(terminal.body(out[:200], "\n-# ✅"), "the message")
        self.assertIn(NOTE, terminal.body(out[:200], ""))
        self.assertNotIn(PIECE, terminal.body(out, ""), "the tail only")
        f = terminal.full_output(out)
        self.clean(f.fp.read().decode("utf-8"), "the file")


class Startup(Posted):
    def test_the_question_before_it_starts(self):
        choice = {"title": "Allow external imports " + KEY, "text": "imports /x/" + KEY + "/CLAUDE.md",
                  "options": ["Yes, allow " + KEY, "No"]}
        self.clean(startup_ask.body(choice), "the message")
        sent = []

        class Msg(object):
            id = 3

        async def send(text, view=None):
            sent.append((text, view))
            return Msg()
        ch = Ch()
        ch.send = send
        ask = startup_ask.StartupAsk(mock.Mock())
        asyncio.run(ask.post(ch, "s", choice))
        self.clean(sent[0][0], "posted")
        self.assertNotIn(PIECE, " ".join(b.label for b in sent[0][1].children), "the buttons")

    def test_what_was_chosen_in_the_channel_and_in_audit(self):
        """#code-review on ab3a424 (low): #audit is posted to Discord too."""
        choice = {"title": "Allow external imports " + KEY, "text": "x", "options": ["Yes, allow " + KEY, "No"]}
        edits, audits = [], []

        class Msg(object):
            id = 3

            async def edit(self, view=None, content=None):
                edits.append(content)

        async def send(text, view=None):
            return Msg()

        async def audit(line):
            audits.append(line)

        class Pane(object):
            t = "s"

            async def raw_screen(self):
                return "view"

            async def keys(self, *k):
                pass

        import discord

        class Click(object):
            type = discord.InteractionType.component
            user = mock.Mock(id=0)
            response = mock.Mock(defer=mock.AsyncMock())

        ch = Ch()
        ch.send = send
        ch.get_partial_message = lambda mid: Msg()
        Click.channel = ch
        ask = startup_ask.StartupAsk(mock.Mock(host=mock.Mock(audit=audit)))
        asyncio.run(ask.post(ch, "s", choice))
        token = next(iter(ask.open))
        from bot import config, tmuxio
        from altention.connectors.claude_code import screen as cc_screen
        with mock.patch.object(config, "OWNER_ID", 0), mock.patch.object(tmuxio, "pane", lambda p: Pane()), \
                mock.patch.object(cc_screen, "startup_choice", lambda v: dict(choice)), \
                mock.patch.object(cc_screen, "keys_for", lambda now, i: ["Enter"]):
            Click.data = {"custom_id": "%s:%s:0" % (startup_ask.PREFIX, token)}
            self.assertTrue(asyncio.run(ask.on_interaction(Click())))
        self.assertTrue(edits and audits)
        for text in edits + audits:
            self.assertNotIn(PIECE, text)


if __name__ == "__main__":
    unittest.main()
