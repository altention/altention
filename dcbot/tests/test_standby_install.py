"""C-163 d2a: a standby gets the supervisor and the bot's venv; one without the bot's token only tells (its supervisor
ends at once, never waiting in the gate for nothing)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py; 11-08: one left a real dcremote-owner)
import importlib.util
import os
import sys
import tempfile
import unittest
from unittest import mock

from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_supervisor():
    spec = importlib.util.spec_from_file_location("supervisor_d2a", os.path.join(HERE, "supervisor.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TokenThere(unittest.TestCase):
    def test_one_answer_for_install_and_the_supervisor(self):
        from altention import config
        sup = load_supervisor()
        d = tempfile.mkdtemp()
        tok, old = os.path.join(d, "t"), os.path.join(d, "o")
        with mock.patch.object(config, "BOT_TOKEN", tok), mock.patch.object(config, "BOT_OLD_SETTINGS", old):
            self.assertFalse(sup.token_there())
            open(tok, "w").close()
            self.assertFalse(sup.token_there(), "empty: not there")
            with open(old, "w") as f:
                f.write("BRIDGE=tmux\nMODEL=x\n")
            self.assertFalse(config.bot_token_there(), "an old settings file without the token: not there")
            with open(old, "a") as f:
                f.write("DISCORD_TOKEN=x\n")
            self.assertTrue(sup.token_there())
            os.remove(old)
            with open(tok, "w") as f:
                f.write("x")
            self.assertTrue(config.bot_token_there())


class NotifyOnlyStandby(unittest.TestCase):
    def run_main(self, role, token):
        sup = load_supervisor()
        state = tempfile.mkdtemp()
        with open(os.path.join(state, "lease-role"), "w") as f:
            f.write(role)
        slept = []
        gates = []
        with mock.patch.object(sup, "STATE", state), mock.patch.object(sup, "not_this_machines_engine", lambda: None), \
                mock.patch.object(sup, "lease_gate", lambda crashing=False: gates.append(1) or "wait"), \
                mock.patch.object(sup, "token_there", lambda: token), \
                mock.patch.object(sup, "standby_here", lambda: role == "standby"), \
                mock.patch.object(sup, "log", lambda *a: None), \
                mock.patch.object(sup.time, "sleep", lambda s: (slept.append(s), (_ for _ in ()).throw(KeyboardInterrupt))[1]):
            try:
                return sup.main([]), gates
            except KeyboardInterrupt:
                return "waited", gates

    def test_a_standby_without_the_token_ends(self):
        self.assertEqual(self.run_main("standby", False), (0, []), "before the gate: Discord not asked with no token")

    def test_with_the_token_it_waits_as_before(self):
        self.assertEqual(self.run_main("standby", True)[0], "waited")
        self.assertEqual(self.run_main("primary", False)[0], "waited", "the primary: as before")


class StandbyHere(unittest.TestCase):
    def test_the_gate_said_or_the_list_says(self):
        sup = load_supervisor()
        state = tempfile.mkdtemp()
        from altention import config
        with mock.patch.object(sup, "STATE", state), mock.patch.object(config, "this_machine", lambda: "host-b"), \
                mock.patch.object(config, "inventory", lambda: {"machines": {"host-b": {"roles": ["dcbot-standby"]}}}):
            self.assertTrue(sup.standby_here(), "its first run: by the list")
        with mock.patch.object(sup, "STATE", state), mock.patch.object(config, "this_machine", lambda: "host-b"), \
                mock.patch.object(config, "inventory", lambda: {"machines": {"host-b": {"roles": []}}}):
            self.assertFalse(sup.standby_here())
            with open(os.path.join(state, "lease-role"), "w") as f:
                f.write("standby")
            self.assertFalse(sup.standby_here(), "the role taken away: the list counts, not an old lease-role")
        with mock.patch.object(sup, "STATE", state), mock.patch.object(config, "inventory", lambda: {}):
            self.assertTrue(sup.standby_here(), "no list: what the gate said")


class Install(unittest.TestCase):
    def test_the_standby_installs_the_bot(self):
        sys.path.insert(0, os.path.join(os.path.dirname(HERE), "ops"))
        import install
        from altention import config, plugin
        r = mock.Mock()
        rows = [("core", "/e/altention", {"name": "core"}, "on", "")]
        with mock.patch.object(config, "settings", lambda: {}), mock.patch.object(config, "inventory", lambda: {"machines": {}}), \
                mock.patch.object(plugin, "on_this_machine", lambda *a: (rows, [])), \
                mock.patch.object(config, "bot_machine", lambda: ("host-a", None, False)), \
                mock.patch.object(config, "this_machine", lambda: "host-b"), \
                mock.patch.object(plugin, "standby_machines", lambda: {"host-b"}):
            found, bot_here = install.plugins_here(r, [])
        self.assertEqual((found, bot_here), ([("dcbot", "dcbot")], False))
        self.assertTrue(install.plugins_here.standby)
        from altention import botmove
        with mock.patch.object(botmove, "move", lambda tell: (0, "", False)), \
                mock.patch.object(config, "bot_token_there", lambda: False), \
                mock.patch.object(config, "bot_machine", lambda: ("host-a", None, False)):
            install.token(r, False)
        said = r.say.call_args_list[-1].args
        self.assertEqual(said[:2], ("ok", "dcbot.token"))
        self.assertEqual(said[2], "這台是備援：沒有機器人的 token，host-a 停了只會通知你，不會接手（要自動接手，到 #setup 同意）。")


if __name__ == "__main__":
    unittest.main()
