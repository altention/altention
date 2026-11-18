"""altention.health (C-163 d3b, design section 6): the outside dead man's switch. In a throwaway home; nothing sent."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import io
import json
import os
import tempfile
import time
import unittest
from unittest import mock

from altention import health


class Health(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.url_file = os.path.join(d, "url")
        self.engine = os.path.join(d, "engine")
        os.makedirs(os.path.join(self.engine, "dcbot", "state"))
        for p in (mock.patch.object(health.secret_store, "path", lambda plugin, name: self.url_file),
                  mock.patch.object(health, "ENGINE", self.engine)):
            p.start()
            self.addCleanup(p.stop)

    def put(self, url=None, beat=None):
        if url is not None:
            with open(self.url_file, "w") as f:
                f.write(url + "\n")
        if beat is not None:
            with open(os.path.join(self.engine, "dcbot", "state", "heartbeat"), "w") as f:
                f.write(str(int(time.time()) - beat))

    def test_only_https(self):
        self.assertTrue(health.allowed("https://hc-ping.com/abc"))
        self.assertTrue(health.allowed("https://hc.example.org/ping/abc"), "the owner's own https server")
        for url in ("http://hc-ping.com/abc", "ftp://x", "https://", "hc-ping.com/abc", ""):
            self.assertFalse(health.allowed(url), url)

    def test_each_case(self):
        sent = []

        def opener(req, timeout):
            sent.append((req.full_url, req.get_method(), req.get_header("User-agent"), req.data, timeout))
            return io.BytesIO(b"OK")
        self.assertEqual(health.ping(opener=opener), "no address")
        self.put(url="http://hc-ping.com/abc", beat=10)
        self.assertEqual(health.ping(opener=opener), "refused")
        self.put(url="https://hc-ping.com/abc", beat=300)
        self.assertEqual(health.ping(opener=opener), "bot not fresh", "a bot stuck: its heartbeat old, no ping")
        self.put(beat=10)
        self.assertEqual(health.ping(opener=opener), "pinged")
        self.assertEqual(sent, [("https://hc-ping.com/abc", "GET", "altention", None, 10)], "nothing but a GET")

        def broken(req, timeout):
            raise OSError("off the net")
        self.assertEqual(health.ping(opener=broken), "failed: OSError")

    def test_said_once_in_the_log(self):
        self.put(url="http://hc-ping.com/abc", beat=10)
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            for _ in range(3):
                health.main(["ping"])
        self.assertEqual(out.getvalue().count("不是 https"), 1)


class Schedule(unittest.TestCase):
    def test_in_the_core_on_the_bots_machine_and_may_be_off(self):
        from altention import plugin
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "core.plugin.json"), encoding="utf-8") as f:
            d = json.load(f)
        item = next(c for c in d["cron"] if c["name"] == "healthchecks")
        self.assertEqual((item["on"], item["when"]), (["bot", "standby"], {"every": "5m"}),
                         "whichever machine is the bot pings (its heartbeat fresh): no false down at a takeover")
        self.assertIn("healthchecks", plugin.CORE_OVERRIDABLE)
        self.assertNotIn("healthchecks", plugin.CORE_ALWAYS_ON)
        self.assertIn("healthchecks-url", [s["name"] for s in d["secrets"]])


if __name__ == "__main__":
    unittest.main()
