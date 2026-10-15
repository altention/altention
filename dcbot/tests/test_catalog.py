"""Claude Code's commands and skills as Discord menu entries."""
import asyncio
import os
import unittest
from unittest import mock

from bot import catalog
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)


class Catalog(unittest.TestCase):
    def test_build(self):
        found = {"slash_commands": ["council", "deep-research", "anthropic-skills:deep-research", "anthropic-skills:pdf",
                                    "anthropic-skills:morning", "model", "status", "clear", "My:Bad Name"],
                 "skills": ["council", "deep-research", "anthropic-skills:deep-research", "anthropic-skills:pdf",
                            "anthropic-skills:morning", "My:Bad Name"]}
        entries = catalog.build(found, {"council": "多視角議會：派多個角色分析同一個問題。然後還有很多說明。"}, reserved=("status",))
        self.assertEqual([(e["name"], e["run"]) for e in entries],
                         [("council", "council"), ("deep-research", "deep-research"),
                          ("morning", "anthropic-skills:morning"), ("model", "model")])
        self.assertEqual(entries[0]["description"], "Skill：多視角議會：派多個角色分析同一個問題。")
        self.assertTrue(all(len(e["description"]) <= 100 for e in entries))
        long = catalog.build({"slash_commands": ["x"], "skills": ["x"]}, {"x": "字" * 300})
        self.assertEqual(len(long[0]["description"]), 100)

    def test_asking_goes_to_the_connector_with_the_bots_program_and_a_helpers_environment(self):
        """What must not reach a helper `claude` is the bot's to know (its own variables, the pane it runs in)."""
        seen = []

        async def ask(claude, cwd, env, timeout=60):
            seen.append((claude, cwd, timeout, sorted(k for k in ("TMUX", "TMUX_PANE", "DCBOT_ROOT", "DCBOT_CHANNEL", "KEPT") if k in env),
                         env.get("ALTENTION_BACKGROUND"), "CHATLOG_SKIP" in env))
            return {"slash_commands": ["x"], "skills": []}

        with mock.patch.object(catalog.cc_commands, "ask", ask), mock.patch.object(catalog.config, "CLAUDE", "/the/claude"), \
                mock.patch.dict(os.environ, {"TMUX": "/s,1,0", "TMUX_PANE": "%1", "DCBOT_ROOT": "/r", "DCBOT_CHANNEL": "5", "KEPT": "1"}):
            self.assertEqual(asyncio.run(catalog.ask_claude("/w", 7)), {"slash_commands": ["x"], "skills": []})
        self.assertEqual(seen, [("/the/claude", "/w", 7, ["KEPT"], "1", False)], "and marked as a background Claude (stage 3b step 1.9b)")
        self.assertNotIn("ALTENTION_BACKGROUND", os.environ, "the mark is the helper's, not the bot's own")

    def test_stale(self):
        self.assertTrue(catalog.stale(None))
        self.assertTrue(catalog.stale({"ts": 0}))
        self.assertEqual(catalog.first_sentence("第一句已經夠長了所以在這裡斷開。第二句。"), "第一句已經夠長了所以在這裡斷開。")


if __name__ == "__main__":
    unittest.main()
