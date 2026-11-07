"""altention.offers (C-64, C-77 G4): which official plugins may be offered, the question's words and the answers
kept (three laters are a no)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import unittest

from altention import offers, plugin

LISTED = {"rig-cal": {"plugin": {"name": "rig-cal", "title": "日曆", "pitch": "把行程接上你的日曆。",
                                 "offer": {"when": "asked"},
                                 "guide": [{"id": "a", "title": "A", "check": None, "ask": "?"},
                                           {"id": "o", "title": "O", "check": None, "ask": "?", "optional": True}]}},
          "rig-pdf": {"plugin": {"name": "rig-pdf", "title": "PDF", "pitch": "看 PDF。", "offer": {"cost": "約 10MB"},
                                 "needs": [{"name": "x", "check": "true", "install": {"debian": "x"}}]}},
          "rig-tools": {"plugin": {"name": "rig-tools", "title": "工具", "pitch": "p", "offer": {"when": "install"},
                                   "needs": [{"name": "pdftool", "check": "true", "size": "約 5MB", "install": {"macos": "x"}},
                                             {"name": "bare", "check": "true"}]}},
          "rig-plain": {"plugin": {"name": "rig-plain", "title": "P", "pitch": "p"}}}


class Offers(unittest.TestCase):
    def test_candidates_and_moments(self):
        self.assertEqual(sorted(offers.candidates(LISTED, {})), ["rig-cal", "rig-pdf", "rig-tools"], "no offer: never offered")
        self.assertEqual(sorted(offers.candidates(LISTED, {"plugins": {"rig-pdf": {"disabled_on": ["lap"]}}})),
                         ["rig-cal", "rig-tools"], "one the instance names is decided already")
        self.assertEqual(offers.due(("install",), LISTED, {}), ["rig-pdf", "rig-tools"], "install is the default")
        self.assertEqual(offers.due(("asked",), LISTED, {}), ["rig-cal"])
        s = {}
        offers.record(s, "rig-tools", "later")
        offers.record(s, "rig-pdf", "no")
        self.assertEqual(offers.due(("install",), LISTED, s), ["rig-tools"], "a later and a never-answered: a no is not")

    def test_answers_three_laters_are_a_no(self):
        s = {}
        self.assertEqual(offers.record(s, "rig-pdf", "later"), "later")
        self.assertTrue(offers.open_for("rig-pdf", s))
        offers.record(s, "rig-pdf", "later")
        self.assertEqual(offers.record(s, "rig-pdf", "later"), "no")
        self.assertEqual((s["offers"]["rig-pdf"]["answer"], s["offers"]["rig-pdf"]["times"]), ("no", 3))
        self.assertFalse(offers.open_for("rig-pdf", s))
        offers.record(s, "rig-tools", "yes")
        self.assertFalse(offers.open_for("rig-tools", s))
        self.assertEqual(offers.due(("install",), LISTED, s), [])
        self.assertTrue(offers.open_for("rig-pdf", {"offers": "broken"}))
        s = {"offers": "broken"}
        offers.record(s, "rig-cal", "no")
        self.assertEqual(s["offers"]["rig-cal"]["answer"], "no")

    def test_the_question_and_its_cost(self):
        q = offers.question("rig-tools", LISTED["rig-tools"]["plugin"])
        self.assertEqual((q["key"], q["values"], q["header"]), ("offer.rig-tools", ["yes", "no", "later"], "可選功能"))
        self.assertEqual(q["question"], "工具：p\n需要：要裝 pdftool（約 5MB）。要開嗎？")
        self.assertIn("到 #setup 說「裝 rig-tools」", q["options"][1]["description"])
        self.assertEqual([o["label"] for o in q["options"]], ["好，開", "先不要", "之後再說"])
        self.assertIn("最多 3 次", q["options"][2]["description"])
        self.assertEqual(offers.cost(LISTED["rig-pdf"]["plugin"]), "約 10MB", "its own words first")
        self.assertEqual(offers.cost(LISTED["rig-cal"]["plugin"]), "要你到別的網站或 App 辦 1 件事", "optional steps left out")
        self.assertEqual(offers.cost({"offer": {}}), "不用另外裝東西")
        self.assertEqual(offers.cost({"offer": {"cost": "約 <@42> 10MB\n見 [說明](https://example.org)" + "很" * 300}})[:16],
                         "約 10MB 見 說明很很很很很", "made plain: no mentions, links as their text, one line")
        self.assertEqual(len(offers.cost({"offer": {"cost": "很" * 300}})), offers.COST_MOST)

    def test_when_is_one_of_three(self):
        base = {"format": 1, "name": "demo", "title": "D", "description": "d"}
        for when in plugin.OFFER_WHEN:
            plugin.check(dict(base, offer={"when": when}))
        for when in ("sometimes", "new"):  # no "new": a plugin new on the list is not asked about (owner 10-21)
            with self.assertRaises(plugin.PluginError):
                plugin.check(dict(base, offer={"when": when}))


if __name__ == "__main__":
    unittest.main()
