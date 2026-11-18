"""C-172a P1c: what a message from a tool may do (bot/rules.py): pings, @everyone, custom_ids, which message it may
change. Pure functions, not wired in yet."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import unittest

from bot import rules
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)

ME, OTHER = "頻道 5 的 Claude（自稱）", "頻道 6 的 Claude（自稱）"


def row(*parts):
    return {"type": 1, "components": list(parts)}


def button(custom_id=None, style=1, **kw):
    b = dict({"type": 2, "style": style, "label": "x"}, **kw)
    if custom_id is not None:
        b["custom_id"] = custom_id
    return b


class Pings(unittest.TestCase):
    def test_the_owner_always(self):
        self.assertIsNone(rules.ping_refused(["owner"]))
        self.assertIsNone(rules.ping_refused([]))
        self.assertIsNone(rules.ping_refused(None))
        self.assertEqual(rules.mentions(["owner"], 42), {"parse": [], "users": ["42"]})
        self.assertEqual(rules.mentions([], 42), {"parse": []})

    def test_users_and_roles_only_when_the_instance_says_any(self):
        for ping in (["user:7"], ["role:8"], ["owner", "user:7"]):
            self.assertIn("json_mentions", rules.ping_refused(ping), ping)
            self.assertIsNone(rules.ping_refused(ping, allow="any"), ping)
        self.assertEqual(rules.mentions(["owner", "user:7", "role:8", "user:42"], 42),
                         {"parse": [], "users": ["42", "7"], "roles": ["8"]})
        self.assertEqual(rules.mentions(["owner"], 0), {"parse": []}, "no owner set: nobody")

    def test_everyone_and_here_never(self):
        for ping in (["everyone"], ["@here"], ["owner", "@everyone"]):
            self.assertIn("@everyone", rules.ping_refused(ping, allow="any"), ping)

    def test_a_ping_not_understood(self):
        for ping in (["user:abc"], ["admin"], ["role:"]):
            self.assertIn("owner、user:ID 或 role:ID", rules.ping_refused(ping, allow="any"), ping)


class Everyone(unittest.TestCase):
    def test_in_content_or_a_text_display(self):
        self.assertTrue(rules.everyone_refused({"content": "hi @everyone"}))
        self.assertTrue(rules.everyone_refused({"content": "@here look"}))
        v2 = {"flags": 32768, "components": [{"type": 17, "components": [{"type": 10, "content": "all @here"}]}]}
        self.assertTrue(rules.everyone_refused(v2))
        self.assertIn("沒有改", rules.everyone_refused({"content": "@here"}, "edit"))

    def test_not_in_an_embed_nor_a_lookalike(self):
        self.assertIsNone(rules.everyone_refused({"content": "hi", "embeds": [{"description": "@everyone"}]}))
        self.assertIsNone(rules.everyone_refused({"content": "mail me@everyones.example"}))
        self.assertIsNone(rules.everyone_refused({"content": "no one"}))


class OwnerPinged(unittest.TestCase):
    def test_only_when_the_words_name_the_owner_and_it_is_let_through(self):
        let = {"parse": [], "users": ["42"]}
        self.assertTrue(rules.owner_pinged({"content": "<@42> hi"}, 42, let))
        self.assertTrue(rules.owner_pinged({"content": "<@!42>", "allowed_mentions": let}, 42))
        self.assertFalse(rules.owner_pinged({"content": "hi"}, 42, let), "--mention owner alone pings nobody")
        self.assertFalse(rules.owner_pinged({"content": "<@42>"}, 42, {"parse": []}), "not let through")
        self.assertFalse(rules.owner_pinged({"content": "<@420>"}, 42, let))
        self.assertFalse(rules.owner_pinged({"content": "x", "embeds": [{"description": "<@42>"}]}, 42, let),
                         "an embed's words never ping")
        self.assertFalse(rules.owner_pinged({"content": "<@42>"}, 0, let), "no owner set")


class Buttons(unittest.TestCase):
    def test_user_prefix_and_link_buttons_pass(self):
        body = {"components": [row(button("user:a"), button(style=5, url="https://example.com"))]}
        self.assertIsNone(rules.buttons_refused(body))
        self.assertIsNone(rules.buttons_refused({"content": "no components"}))

    def test_anything_else_with_a_custom_id_is_refused(self):
        for body in ({"components": [row(button("ask:1"))]},
                     {"components": [row({"type": 3, "custom_id": "pick", "options": []})]},
                     {"flags": 32768, "components": [{"type": 17, "components": [
                         {"type": 9, "components": [{"type": 10, "content": "t"}], "accessory": button("resend:1")}]}]}):
            self.assertIn("user:", rules.buttons_refused(body), body)

    def test_the_custom_ids_found_through_every_layer(self):
        body = [{"type": 17, "components": [row(button("a")), {"type": 9, "accessory": button("b")}]}, row(button("c"))]
        self.assertEqual(rules.custom_ids(body), ["a", "b", "c"])
        self.assertEqual(rules.custom_ids("not a list"), [])


class Change(unittest.TestCase):
    def test_a_post_of_ones_own(self):
        self.assertIsNone(rules.change_refused({"intent": "post", "from": ME}, ME))

    def test_refused_with_why(self):
        for entry, word in ((None, "ledger"), ({"intent": "card", "from": ME}, "卡片"),
                            ({"intent": "board", "from": ME}, "--board"), ({"intent": "reply", "from": None}, "不是用工具發"),
                            ({"intent": "status", "from": None}, "不是用工具發"), ({"intent": "post", "from": OTHER}, OTHER)):
            self.assertIn(word, rules.change_refused(entry, ME), entry)


class Refused(unittest.TestCase):
    def test_first_reason_given(self):
        self.assertIsNone(rules.refused({"content": "hi"}, ["owner"]))
        self.assertIn("@everyone", rules.refused({"content": "@everyone"}))
        self.assertIn("user:", rules.refused({"components": [row(button("x"))]}))
        self.assertIn("json_mentions", rules.refused({"content": "hi"}, ["user:7"]))


if __name__ == "__main__":
    unittest.main()
