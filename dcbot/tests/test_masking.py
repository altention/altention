"""B-12: what the bot posts of Claude's words and of its tools' output is masked first (bot/masking.py, on
altention/redact.py's pattern), and a message that had something masked says so in a small line."""
import asyncio
import unittest
from unittest import mock

from bot import masking, messages, sessions
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

# made up, and put together here so that no whole key stands in this file
KEY = "sk-ant-" + "a" * 30
DISCORD = "MTIzNDU2Nzg5MDEyMzQ1Njc4OQ.GaBcDe." + "d" * 30
PEM = "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\nAAAABG5vbmUAAAAEbm9uZQ==\n-----END OPENSSH PRIVATE KEY-----"


class Mask(unittest.TestCase):
    def test_keys_are_masked_and_counted(self):
        out, n = masking.mask("token %s and %s" % (KEY, DISCORD))
        self.assertEqual((out, n), ("token [REDACTED] and [REDACTED]", 2))
        self.assertEqual(masking.masked("x %s" % KEY), "x [REDACTED]\n" + messages.note("masked", n=1))
        self.assertIn("1 處", messages.note("masked", n=1))

    def test_what_stays(self):
        """Hex hashes (git, sha256), ids and UUIDs are no keys (manager: a counter-example in the tests)."""
        for text in ("aa79535", "aa79535" + "0123456789abcdef" * 2 + "f", "0123456789abcdef" * 4, "sha256:" + "fedcba9876543210" * 4,
                     "123e4567-e89b-12d3-a456-426614174000", "see bot/files.py:12", "", "already [REDACTED] in the text"):
            self.assertEqual(masking.mask(text), (text, 0), text)
            self.assertEqual(masking.masked(text), text)

    def test_a_private_key_with_or_without_its_end(self):
        self.assertEqual(masking.mask("before\n" + PEM + "\nafter"), ("before\n[REDACTED]\nafter", 1))
        cut = PEM.split("-----END")[0]
        out, n = masking.mask("before\n" + cut)
        self.assertEqual((out, n), ("before\n[REDACTED]", 1), "its end line missing (a text cut short): masked to the end")

    def test_the_first_part_of_a_key_where_a_text_was_cut(self):
        part = KEY[:15]
        self.assertEqual(masking.mask("output: " + part)[1], 0, "not at a cut: the pattern alone (too short to be one)")
        self.assertEqual(masking.mask("output: " + part, cut=True), ("output: [REDACTED]", 1))
        text = "output: %s\n…（還有 120 字，對這則按 📋 接著看）" % DISCORD[:30]
        out, n = masking.mask(text)
        self.assertEqual(n, 1)
        self.assertTrue(out.startswith("output: [REDACTED]\n…（還有 120 字"), out)
        self.assertEqual(masking.mask("ls -la\n…（還有 120 字，對這則按 📋 接著看）")[1], 0, "ordinary words before a cut stay")


    def test_only_a_word_of_its_own_is_taken_for_a_key_at_a_cut(self):
        """(#code-review on b3370e8) ordinary words at a cut stay."""
        for text in ("/var/log/NotificationDispatcherService", "run disk-usage-report", "MAXIMUMLENGTHOFTHISIDENTIFIER", "see risk-assessment"):
            self.assertEqual(masking.mask(text, cut=True), (text, 0), text)
        for text in ("token " + KEY[:12], "x=" + DISCORD[:25], "(" + KEY[:10]):
            self.assertEqual(masking.mask(text, cut=True)[1], 1, text)

    def test_masked_before_cut(self):
        """(#code-review on b3370e8) a key across the limit: masked whole, then cut; the small line kept."""
        text = "x" * 3880 + " " + "sk-A1b2C3d4E5f6G7h8I9j0K1l2"
        out = masking.masked(text, limit=3850)
        self.assertNotIn("sk-A1b2", out)
        self.assertTrue(out.endswith(messages.note("masked", n=1)))


class WherePosted(unittest.TestCase):
    def test_histories(self):
        rows = [("typed", "my key is %s" % KEY, "I will not repeat %s" % KEY, "2026-10-14T01:02:03Z")]
        posted = "\n".join(sessions.messages_of(rows))
        self.assertNotIn(KEY, posted)
        self.assertIn("[REDACTED]", posted)
        recap = sessions.recap_text([("see %s" % KEY, "ok %s" % KEY)])
        self.assertNotIn(KEY, recap)

    def test_a_reply_and_what_was_said_along_the_way(self):
        from bot import tmux_bridge
        from tests.test_turn_end import Ch, Host
        ch = Ch()
        bridge = tmux_bridge.TmuxBridge(Host(ch))
        st, rt = bridge.host.chan(ch), bridge.rt(ch)
        rt.turn, st["busy"] = tmux_bridge.Turn("go"), True

        async def go():
            with mock.patch.object(tmux_bridge.config, "INTERIM", True):
                await bridge.interim(ch, rt, texts=["printed %s" % KEY])
            await bridge.handle({"channel": str(ch.id), "hook_event_name": "Stop", "session_id": "s",
                                 "last_assistant_message": "the token was %s" % DISCORD})
        asyncio.run(go())
        said = "\n".join(m.content for m in ch.sent)
        self.assertNotIn(KEY, said)
        self.assertNotIn(DISCORD, said)
        self.assertIn(messages.note("masked", n=1), said)

    def test_the_places_that_post_claudes_words_go_through_it(self):
        """A guard on the source: each of these posts what it posts through masking."""
        import inspect
        from bot import agents, process, tmux_bridge
        for function, times in ((process.show, 3), (process.expand_now, 1), (sessions.messages_of, 1), (sessions.recap_text, 1),
                                (tmux_bridge.TmuxBridge.interim, 1), (tmux_bridge.TmuxBridge.progress, 1), (agents.AgentThreads.flush if hasattr(agents, "AgentThreads") else None, 0)):
            if function is None:
                continue
            self.assertGreaterEqual(inspect.getsource(function).count("masking."), times, function.__qualname__)
        self.assertIn("masking.mask(\" \".join(first.split(\"\\n\", 1)[-1].split()))[0][:80]", inspect.getsource(process.show), "the title masked before cut")
        handle = inspect.getsource(tmux_bridge.TmuxBridge.handle)
        for spot in ('masking.masked(ev.get("last_assistant_message")', 'masking.masked(ev.get("dc_text") or "", limit=', 'masking.masked(ev.get("send_text")',
                     'masking.masked(prompt)'):
            self.assertIn(spot, handle)


if __name__ == "__main__":
    unittest.main()
