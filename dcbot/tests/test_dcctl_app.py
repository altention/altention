"""dcctl app (C-54a): the bot's application for a plugin: whether its Activities are on, and the IDs it needs; never
the token."""
import importlib.machinery
import importlib.util
import io
import json
import os
import unittest
from unittest import mock
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
loader = importlib.machinery.SourceFileLoader("dcctl_app", os.path.join(ROOT, "tools", "dcctl"))
dcctl = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcctl_app", loader))
loader.exec_module(dcctl)
TOKEN = "TOKEN-NEVER-SHOWN"


class App(unittest.TestCase):
    def run_cmd(self, argv, app):
        calls, out = [], io.StringIO()

        def call(method, path, body=None):
            calls.append((method, path))
            return app
        with mock.patch.object(dcctl, "call", call), mock.patch.object(dcctl.config, "TOKEN", TOKEN), \
                mock.patch.object(dcctl.config, "OWNER_ID", 42), mock.patch.object(dcctl.config, "GUILD_ID", 500), \
                mock.patch("sys.stdout", out):
            dcctl.main(argv)
        self.assertEqual(calls, [("GET", "/applications/@me")], "one read, nothing written (no #audit line either)")
        self.assertNotIn(TOKEN, out.getvalue())
        return out.getvalue()

    def test_json_for_a_plugin(self):
        got = json.loads(self.run_cmd(["app", "--json"], {"id": "123", "flags": 2228224}))  # measured: an app with Activities on
        self.assertEqual(got, {"activities": True, "app_id": "123", "owner_id": "42", "guild_id": "500"})
        got = json.loads(self.run_cmd(["app", "--json"], {"id": "123", "flags": 8192}))
        self.assertFalse(got["activities"])
        self.assertFalse(json.loads(self.run_cmd(["app", "--json"], {"id": "123"}))["activities"], "flags is optional")

    def test_for_people(self):
        out = self.run_cmd(["app"], {"id": "123", "flags": 1 << 17})
        self.assertIn("Activities：開", out)
        self.assertIn("123", out)


if __name__ == "__main__":
    unittest.main()
